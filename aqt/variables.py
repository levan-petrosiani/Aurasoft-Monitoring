"""
Expands {{...}} placeholders:
  uuid                                   a new GUID every time
  date.today, date.today+2d, date.today-5d   yyyy-MM-dd (UTC)
  datetime.now                           ISO 8601 UTC
  bank.bic, bank.name, tpp.redirectUri, tpp.id
  vars.path.to.value                     profile `variables`
  env.NAME                               environment variable
  any other name                         a value saved by an earlier step
"""

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import jsonpath
from .errors import ConfigurationError
from .profiles import BankProfile

_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")
_DATE = re.compile(r"^date\.today(?:([+-])(\d+)d)?$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VariableResolver:
    def __init__(self, profile: BankProfile, tpp_id: str | None, now: Callable[[], datetime] | None = None):
        self._profile = profile
        self._tpp_id = tpp_id
        self._now = now or utc_now
        self.saved: dict[str, str] = {}

    def save(self, name: str, value: str):
        self.saved[name] = value

    def is_saved(self, name: str) -> bool:
        return name in self.saved

    def resolve(self, text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: self._evaluate(m.group(1).strip()), text)

    def try_resolve(self, text: str) -> str | None:
        """Like resolve, but returns None instead of raising when a variable is unknown."""
        try:
            return self.resolve(text)
        except ConfigurationError:
            return None

    def resolve_node(self, node):
        """Returns a deep copy of a JSON value with every string resolved."""
        if isinstance(node, str):
            return self.resolve(node)
        if isinstance(node, dict):
            return {k: self.resolve_node(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self.resolve_node(v) for v in node]
        return node

    def _evaluate(self, expr: str) -> str:
        if expr == "uuid":
            return str(uuid.uuid4())

        if expr == "datetime.now":
            return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")

        m = _DATE.match(expr)
        if m:
            day = self._now().date()
            if m.group(1):
                n = int(m.group(2))
                day = day + timedelta(days=-n if m.group(1) == "-" else n)
            return day.strftime("%Y-%m-%d")

        if expr == "bank.bic":
            return self._profile.bank.bic
        if expr == "bank.name":
            return self._profile.bank.name
        if expr == "tpp.redirectUri":
            return self._profile.tpp.redirect_uri
        if expr == "tpp.id":
            if self._tpp_id is None:
                raise ConfigurationError("{{tpp.id}} needs a QWAC with an organizationIdentifier")
            return self._tpp_id

        if expr.startswith("env."):
            value = os.environ.get(expr[4:])
            if value is None:
                raise ConfigurationError(f"Environment variable '{expr[4:]}' is not set")
            return value

        if expr.startswith("vars."):
            matches = jsonpath.select(self._profile.variables, "$." + expr[5:])
            if not matches:
                raise ConfigurationError(f"Profile variable '{expr[5:]}' is not defined in {self._profile.source_path}")
            return jsonpath.as_text(matches[0])

        if expr in self.saved:
            return self.saved[expr]

        raise ConfigurationError(f"Unknown variable '{{{{{expr}}}}}'")
