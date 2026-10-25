"""Which provider and model each AI feature uses. Defaults: drafting reads only public standards -> Claude; summaries
and the ask-agent see run data -> Claude by default, switchable to a local model (Ollama) to keep data on the machine.

Overrides come from the environment, so the CLI and the portal share them:
  AQT_AI_<FEATURE>_PROVIDER = anthropic | ollama     (FEATURE: SUMMARY, ASK, RULE_DRAFTING, SCENARIO_DRAFTING, CHECK)
  AQT_AI_<FEATURE>_MODEL, AQT_AI_<FEATURE>_EFFORT, AQT_AI_<FEATURE>_MAX_TOKENS
  ANTHROPIC_API_KEY (or an `ant auth login` profile) · AQT_OLLAMA_URL · AQT_OLLAMA_MODEL · AQT_AI_REFUSAL_FALLBACK=0
"""

import os
from dataclasses import dataclass, field, replace

SUMMARY = "summary"
ASK = "ask"
RULE_DRAFTING = "ruleDrafting"
SCENARIO_DRAFTING = "scenarioDrafting"
CHECK = "check"
FEATURES = (SUMMARY, ASK, RULE_DRAFTING, SCENARIO_DRAFTING, CHECK)

ANTHROPIC = "anthropic"
OLLAMA = "ollama"
PROVIDERS = (ANTHROPIC, OLLAMA)


@dataclass
class FeatureOptions:
    provider: str | None = None
    model: str | None = None
    max_output_tokens: int | None = None
    # Claude only: low | medium | high | xhigh | max (adaptive thinking depth and token spend).
    effort: str | None = None


@dataclass
class AnthropicOptions:
    # Empty: ANTHROPIC_API_KEY or the `ant auth login` profile is used. Never commit a key.
    api_key: str | None = None
    default_model: str = "claude-opus-5-5"
    # If Claude declines a request for policy reasons, re-run it on Anthropic's recommended fallback model
    # (fallbacks: "default"). Security-testing material (signatures, tokens, auth flows) can trip classifiers.
    refusal_fallback: bool = True
    base_url: str | None = None  # tests and proxies


@dataclass
class OllamaOptions:
    url: str = "http://localhost:11434"
    # qwen3:8b (5 GB): a TBC run summarized in ~35 s with the right root cause; gemma4:26b (17 GB) was as accurate but
    # took ~190 s. Bigger models can be chosen per feature.
    default_model: str = "qwen3:8b"
    # Local models can take minutes (loading a big model, slow generation).
    timeout_seconds: int = 600
    # Sent as num_ctx. Ollama's default is small and it cuts longer prompts silently, which would drop most of a
    # long clause. 16k fits the largest clauses and still runs on a 24 GB Mac with qwen3:8b.
    context_tokens: int = 16384
    # Local embedding model for clause and rule search (1024 dimensions, multilingual incl. Georgian).
    embedding_model: str = "bge-m3"


DEFAULTS = {
    SUMMARY: FeatureOptions(ANTHROPIC, max_output_tokens=16000, effort="medium"),
    ASK: FeatureOptions(ANTHROPIC, max_output_tokens=16000, effort="medium"),
    RULE_DRAFTING: FeatureOptions(ANTHROPIC, max_output_tokens=32000, effort="high"),
    SCENARIO_DRAFTING: FeatureOptions(ANTHROPIC, max_output_tokens=32000, effort="high"),
    CHECK: FeatureOptions(ANTHROPIC, max_output_tokens=1000, effort="low"),
}


@dataclass
class AiOptions:
    anthropic: AnthropicOptions = field(default_factory=AnthropicOptions)
    ollama: OllamaOptions = field(default_factory=OllamaOptions)
    # Per-feature overrides; features not listed use DEFAULTS.
    features: dict[str, FeatureOptions] = field(default_factory=dict)

    def for_feature(self, feature: str) -> FeatureOptions:
        """Effective settings: configured values over the defaults, the model defaulting per provider."""
        defaults = DEFAULTS.get(feature, FeatureOptions(ANTHROPIC))
        configured = self.features.get(feature) or FeatureOptions()
        provider = configured.provider or defaults.provider or ANTHROPIC
        model = configured.model or (self.ollama.default_model if provider == OLLAMA else self.anthropic.default_model)
        return FeatureOptions(provider, model, configured.max_output_tokens or defaults.max_output_tokens,
                              configured.effort or defaults.effort)

    def with_provider(self, feature: str, provider: str) -> "AiOptions":
        """A copy in which the feature uses `provider` (e.g. a "Local" button). A configured model belongs to the
        configured provider, so switching provider uses the new provider's default model."""
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown AI provider '{provider}' (use {ANTHROPIC} or {OLLAMA})")
        current = self.features.get(feature) or FeatureOptions()
        features = dict(self.features)
        features[feature] = FeatureOptions(provider, current.model if current.provider == provider else None,
                                           current.max_output_tokens, current.effort)
        return replace(self, features=features)

    @staticmethod
    def from_environment(env=os.environ) -> "AiOptions":
        options = AiOptions()
        options.anthropic.api_key = env.get("ANTHROPIC_API_KEY") or None
        options.anthropic.refusal_fallback = env.get("AQT_AI_REFUSAL_FALLBACK", "1") not in ("0", "false", "no")
        options.anthropic.base_url = env.get("ANTHROPIC_BASE_URL") or None
        options.ollama.url = env.get("AQT_OLLAMA_URL", options.ollama.url)
        options.ollama.default_model = env.get("AQT_OLLAMA_MODEL", options.ollama.default_model)
        for feature in FEATURES:
            prefix = "AQT_AI_" + _env_name(feature) + "_"
            fo = FeatureOptions(env.get(prefix + "PROVIDER") or None, env.get(prefix + "MODEL") or None,
                                int(env[prefix + "MAX_TOKENS"]) if env.get(prefix + "MAX_TOKENS") else None,
                                env.get(prefix + "EFFORT") or None)
            if any((fo.provider, fo.model, fo.max_output_tokens, fo.effort)):
                options.features[feature] = fo
        return options


def _env_name(feature: str) -> str:
    """ruleDrafting -> RULE_DRAFTING"""
    return "".join("_" + c if c.isupper() else c for c in feature).upper()
