"""
Who may use the portal. Locally it has no login and answers on localhost only. Hosted (jury mode, e.g. on Railway) it
also answers on the public host names in AQT_PORTAL_HOSTS, and then a password is required (HTTP Basic, user
AQT_PORTAL_USER, password AQT_PORTAL_PASSWORD), model calls started from the portal are capped per day so visitors
cannot spend the Claude key without limit, and deleting runs is turned off.
"""

import hmac
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..errors import ConfigurationError
from ..oauth import is_loopback_host

DEFAULT_USER = "jury"
# Model calls per UTC day started from the portal in jury mode, unless AQT_PORTAL_AI_PER_DAY says otherwise.
DEFAULT_AI_PER_DAY = 20


@dataclass
class PortalAccess:
    """Public host names, login and AI budget. Empty `hosts` = a local portal (no login, no limits)."""
    hosts: list[str] = field(default_factory=list)
    user: str = DEFAULT_USER
    password: str | None = None
    ai_per_day: int | None = None
    _used: dict[str, int] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        self.hosts = [h.strip().lower() for h in self.hosts if h.strip()]
        if self.hosts and not self.password:
            raise ConfigurationError("A portal reachable from outside this machine needs a login: set AQT_PORTAL_PASSWORD "
                                     "(and optionally AQT_PORTAL_USER) together with AQT_PORTAL_HOSTS.")
        if self.hosts and self.ai_per_day is None:
            self.ai_per_day = DEFAULT_AI_PER_DAY

    @staticmethod
    def from_env() -> "PortalAccess":
        limit = os.environ.get("AQT_PORTAL_AI_PER_DAY", "").strip()
        if limit and not limit.isdigit():
            raise ConfigurationError(f"AQT_PORTAL_AI_PER_DAY must be a whole number, got '{limit}'")
        return PortalAccess(hosts=os.environ.get("AQT_PORTAL_HOSTS", "").split(","),
                            user=os.environ.get("AQT_PORTAL_USER") or DEFAULT_USER,
                            password=os.environ.get("AQT_PORTAL_PASSWORD") or None,
                            ai_per_day=int(limit) if limit else None)

    @property
    def public(self) -> bool:
        return bool(self.hosts)

    def allows_host(self, hostname: str | None) -> bool:
        """Localhost always; otherwise only the configured public names (anything else may be DNS rebinding)."""
        return is_loopback_host(hostname) or (hostname or "").lower() in self.hosts

    def check_login(self, authorization) -> bool:
        """True when no password is set or the request carries the right HTTP Basic credentials."""
        if not self.password:
            return True
        if authorization is None or authorization.type != "basic":
            return False
        user_ok = hmac.compare_digest((authorization.username or "").encode(), self.user.encode())
        password_ok = hmac.compare_digest((authorization.password or "").encode(), self.password.encode())
        return user_ok and password_ok

    def take_ai_call(self) -> bool:
        """Counts one model call against today's budget; False when the budget is used up."""
        if self.ai_per_day is None:
            return True
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            used = self._used.get(today, 0)
            if used >= self.ai_per_day:
                return False
            self._used = {today: used + 1}
            return True

    def ai_left_today(self) -> int | None:
        if self.ai_per_day is None:
            return None
        today = datetime.now(timezone.utc).date().isoformat()
        return max(0, self.ai_per_day - self._used.get(today, 0))
