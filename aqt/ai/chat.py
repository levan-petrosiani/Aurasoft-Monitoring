"""
Model providers behind one small interface, and the audit wrapper every call goes through.

- Claude: the official `anthropic` SDK, beta endpoint with `fallbacks: "default"` (refusal fallback), adaptive
  thinking and per-feature effort; streamed (long outputs would hit request timeouts otherwise).
- Ollama: plain HTTP to the local server, `num_ctx` set on every call (its default window silently cuts long prompts),
  JSON-schema output when a schema is given.
- Tools (function calling, used by the ask-agent): one provider-neutral message format —
    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments"}], "raw": <provider's own message>}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
  Claude's assistant turns are sent back as its own content blocks (`raw`): with thinking on, a tool loop must return
  the thinking blocks unchanged.
- AuditingChat: masks the text of outgoing messages (last line of defence — features mask their inputs first) and
  records one ai_calls row per call, successful or not.
"""

import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..store.entities import AiCallRow
from ..store.masking import DEFAULT as DEFAULT_MASKER, SensitiveDataMasker
from . import options as opt

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Tool:
    """A function the model may call: name, what it does, and a JSON schema of its arguments."""
    name: str
    description: str
    parameters: dict


@dataclass
class ChatResult:
    text: str
    model: str
    finish_reason: str              # stop | length | refusal | tool_use
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The provider's own assistant message, sent back as is in the next turn of a tool loop (see the module docstring).
    raw: object = None

    def as_message(self) -> dict:
        """The assistant turn in the neutral format, to append to the conversation."""
        return {"role": "assistant", "content": self.text, "raw": self.raw,
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in self.tool_calls]}


@dataclass
class CallContext:
    """Who/what a model call is for; stored with the audit record. call_id links a feature's own rows to the audit row."""
    run_id: str | None = None
    triggered_by: str | None = None
    call_id: uuid.UUID | None = None


class ProviderError(Exception):
    """The provider could not be reached or answered with an error (also timeouts)."""


class ChatClient:
    provider = ""
    model = ""

    def complete(self, system: str, messages: list[dict], json_schema: dict | None = None,
                 tools: list[Tool] | None = None) -> ChatResult:
        raise NotImplementedError


class AnthropicChat(ChatClient):
    provider = opt.ANTHROPIC

    def __init__(self, options: opt.AiOptions, settings: opt.FeatureOptions):
        import anthropic  # optional dependency (the `ai` extra)

        kwargs = {}
        if options.anthropic.api_key:
            kwargs["api_key"] = options.anthropic.api_key
        if options.anthropic.base_url:
            kwargs["base_url"] = options.anthropic.base_url
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(max_retries=2, **kwargs)
        self._options = options
        self.model = settings.model
        self._settings = settings

    def request(self, system: str, messages: list[dict], tools: list[Tool] | None = None) -> dict:
        """The request body (also used by tests to check the exact shape)."""
        body = {
            "model": self.model,
            "max_tokens": self._settings.max_output_tokens or 16000,
            "system": system,
            "messages": _anthropic_messages(messages),
            "thinking": {"type": "adaptive"},
        }
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        if self._settings.effort:
            body["output_config"] = {"effort": self._settings.effort}
        if self._options.anthropic.refusal_fallback:
            body["betas"] = [FALLBACK_BETA]
            body["fallbacks"] = "default"
        return body

    def complete(self, system, messages, json_schema=None, tools=None) -> ChatResult:
        # The schema is in the system prompt for Claude; structured output together with the fallback is untested.
        body = self.request(system, messages, tools)
        try:
            with self._client.beta.messages.stream(**body) as stream:
                message = stream.get_final_message()
        except self._anthropic.APIError as ex:
            raise ProviderError(f"{type(ex).__name__}: {ex}") from ex
        text = "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text")
        reason = {"end_turn": "stop", "max_tokens": "length", "refusal": "refusal", "tool_use": "tool_use"}.get(
            message.stop_reason or "", message.stop_reason or "stop")
        usage = message.usage
        calls = [ToolCall(b.id, b.name, dict(b.input or {})) for b in message.content if getattr(b, "type", "") == "tool_use"]
        raw = [b.model_dump(exclude_none=True) for b in message.content] if calls else None
        return ChatResult(text, message.model, reason, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
                          calls, raw)


def _anthropic_messages(messages: list[dict]) -> list[dict]:
    """Neutral messages -> Claude messages: tool results are user turns of tool_result blocks (consecutive ones merged)."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            content = m.get("raw") or ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]} for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


class OllamaChat(ChatClient):
    provider = opt.OLLAMA

    def __init__(self, options: opt.AiOptions, settings: opt.FeatureOptions):
        self._options = options.ollama
        self.model = settings.model
        self._max_tokens = settings.max_output_tokens

    def complete(self, system, messages, json_schema=None, tools=None) -> ChatResult:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}] + [_ollama_message(m) for m in messages],
            "options": {"num_ctx": self._options.context_tokens, **({"num_predict": self._max_tokens} if self._max_tokens else {})},
        }
        if json_schema is not None:
            body["format"] = json_schema
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                             for t in tools]
        data = _post_json(self._options.url.rstrip("/") + "/api/chat", body, self._options.timeout_seconds)
        message = data.get("message") or {}
        calls = [ToolCall(c.get("id") or f"call_{uuid.uuid4().hex[:12]}", c["function"]["name"], _arguments(c["function"].get("arguments")))
                 for c in message.get("tool_calls") or [] if c.get("function", {}).get("name")]
        reason = "tool_use" if calls else "length" if data.get("done_reason") == "length" else "stop"
        return ChatResult(message.get("content") or "", data.get("model") or self.model, reason,
                          data.get("prompt_eval_count"), data.get("eval_count"), calls)


def _ollama_message(m: dict) -> dict:
    if m["role"] == "tool":
        return {"role": "tool", "content": m["content"], "tool_name": m.get("name", "")}
    if m["role"] == "assistant" and m.get("tool_calls"):
        return {"role": "assistant", "content": m.get("content") or "",
                "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in m["tool_calls"]]}
    return {"role": m["role"], "content": m["content"]}


def _arguments(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _post_json(url: str, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode("utf-8", "replace")[:300]
        raise ProviderError(f"HTTP {ex.code} from {url}: {detail}") from ex
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise ProviderError(f"{url} is not reachable ({getattr(ex, 'reason', ex)}). Is Ollama running ('ollama serve')?") from ex


class AuditSink:
    def record(self, call: AiCallRow) -> None:
        raise NotImplementedError


class StoreAuditSink(AuditSink):
    """Writes audit records to the ai_calls table. A failed write never loses the answer; it is reported."""

    def __init__(self, store, warn=None):
        self._store = store
        self._warn = warn

    def record(self, call: AiCallRow) -> None:
        try:
            self._store.add_ai_call(call)
        except Exception as ex:  # noqa: BLE001 - the audit failure is reported, the model's answer is still used
            if self._warn:
                self._warn(f"AI audit record {call.id} ({call.feature}) could not be saved: {ex}")


class InMemoryAuditSink(AuditSink):
    """No database configured: keeps the records for the current process (CLI one-offs, tests)."""

    def __init__(self):
        self.calls: list[AiCallRow] = []

    def record(self, call: AiCallRow) -> None:
        self.calls.append(call)


class AuditingChat(ChatClient):
    def __init__(self, inner: ChatClient, feature: str, audit: AuditSink, masker: SensitiveDataMasker, context: CallContext | None):
        self._inner, self._feature, self._audit, self._masker = inner, feature, audit, masker
        self._context = context or CallContext()
        self.provider, self.model = inner.provider, inner.model

    def complete(self, system, messages, json_schema=None, tools=None) -> ChatResult:
        # The text of every turn is masked; Claude's own assistant blocks (`raw`) are the model's output and go back as is.
        masked = [{**m, "content": self._masker.mask_body(m["content"]) or ""} for m in messages]
        system = self._masker.mask_body(system) or ""
        started = time.monotonic()
        request = [{"role": "system", "text": system}] + [_audit_entry(m) for m in masked]
        if tools:
            request.append({"role": "tools", "text": ", ".join(t.name for t in tools)})
        try:
            result = self._inner.complete(system, masked, json_schema, tools) if tools else self._inner.complete(system, masked, json_schema)
        except Exception as ex:
            self._record(request, None, ex, started)
            raise
        self._record(request, result, None, started)
        return result

    def _record(self, request: list[dict], result: ChatResult | None, error: Exception | None, started: float):
        payload = json.dumps(request, ensure_ascii=False)
        self._audit.record(AiCallRow(
            id=self._context.call_id or uuid.uuid4(), time_utc=datetime.now(timezone.utc), feature=self._feature,
            provider=self.provider, model=(result.model if result else None) or self.model,
            prompt_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(), request=request,
            duration_ms=int((time.monotonic() - started) * 1000), succeeded=error is None,
            run_id=self._context.run_id, triggered_by=self._context.triggered_by,
            response=_audit_response(result) if result else None, input_tokens=result.input_tokens if result else None,
            output_tokens=result.output_tokens if result else None,
            error=f"{type(error).__name__}: {error}" if error else None))


def _audit_entry(m: dict) -> dict:
    entry = {"role": m["role"], "text": m["content"]}
    if m.get("tool_calls"):
        entry["toolCalls"] = [{"name": c["name"], "arguments": c["arguments"]} for c in m["tool_calls"]]
    if m["role"] == "tool":
        entry["tool"] = m.get("name")
    return entry


def _audit_response(result: ChatResult) -> str:
    if not result.tool_calls:
        return result.text
    calls = json.dumps([{"name": c.name, "arguments": c.arguments} for c in result.tool_calls], ensure_ascii=False)
    return (result.text + "\n" if result.text else "") + "tool calls: " + calls


@dataclass
class ChatFactory:
    """Creates the chat client for a feature from the options, always wrapped in AuditingChat."""
    options: opt.AiOptions
    audit: AuditSink
    masker: SensitiveDataMasker = DEFAULT_MASKER
    # Tests: replace the provider client / the embedding function.
    provider_override: object = None
    embed_override: object = None

    def describe(self, feature: str) -> opt.FeatureOptions:
        return self.options.for_feature(feature)

    def create(self, feature: str, context: CallContext | None = None) -> ChatClient:
        settings = self.options.for_feature(feature)
        if self.provider_override is not None:
            inner = self.provider_override(settings)
        elif settings.provider == opt.ANTHROPIC:
            inner = AnthropicChat(self.options, settings)
        elif settings.provider == opt.OLLAMA:
            inner = OllamaChat(self.options, settings)
        else:
            raise ValueError(f"Unknown AI provider '{settings.provider}' for feature '{feature}' (use {opt.ANTHROPIC} or {opt.OLLAMA})")
        return AuditingChat(inner, feature, self.audit, self.masker, context)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Local embeddings (bge-m3 via Ollama). Not audited: nothing leaves the machine, and the inputs are public
        standards text and drafted rules."""
        if self.embed_override is not None:
            return self.embed_override(texts)
        o = self.options.ollama
        data = _post_json(o.url.rstrip("/") + "/api/embed", {"model": o.embedding_model, "input": texts}, o.timeout_seconds)
        return data["embeddings"]
