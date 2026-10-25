"""monitoring.yaml: what runs unattended and when, where alerts go, how long exchanges are kept."""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .. import yamljson
from ..binding import bind, required
from ..errors import ConfigurationError

FILE_NAME = "monitoring.yaml"
RETENTION_STATE = "__retention__"
ALERT_KINDS = ("regression", "fixed", "bank_down", "run_failed")
_EVERY = re.compile(r"^(\d+)\s*([mhd])$")
_AT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass
class Schedule:
    name: str = required()
    # Bank profile (BIC or file name), as for `aqt run --bank`.
    bank: str = required()
    suites: list[str] | None = None
    scenarios: list[str] | None = None
    # Exactly one of: every (e.g. 30m, 6h, 1d) or at (daily, HH:MM local time of the machine running the monitor).
    every: str | None = None
    at: str | None = None
    # Start MockBank for the run and approve SCA automatically (localhost profiles only).
    with_mock: bool = False
    enabled: bool = True

    @property
    def interval(self) -> timedelta | None:
        m = _EVERY.match((self.every or "").strip())
        if not m:
            return None
        unit = {"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
        return timedelta(**{unit: int(m.group(1))})

    def describe(self) -> str:
        return f"every {self.every}" if self.every else f"daily at {self.at}"


@dataclass
class AlertsConfig:
    # Environment variable holding a webhook URL (Slack/Teams-style incoming webhook, JSON {"text": ...}). Unset: no
    # notification; alerts are still recorded and shown in the portal.
    webhook_env: str = "AQT_ALERT_WEBHOOK"
    # Which alert kinds are sent to the webhook.
    notify: list[str] = field(default_factory=lambda: list(ALERT_KINDS))


@dataclass
class RetentionConfig:
    # Stored HTTP exchanges older than this are deleted daily; results, summaries and alerts are kept.
    exchanges_days: int = 90


@dataclass
class MonitoringConfig:
    schedules: list[Schedule] = field(default_factory=list)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)

    @property
    def webhook_url(self) -> str | None:
        return os.environ.get(self.alerts.webhook_env) or None


def load(root_directory: str) -> MonitoringConfig:
    """Reads <root>/monitoring.yaml; no file means no schedules and default alerts/retention."""
    path = os.path.join(root_directory, FILE_NAME)
    if not os.path.isfile(path):
        return MonitoringConfig()
    raw = yamljson.parse_file(path)
    if raw is None:
        return MonitoringConfig()
    try:
        config = bind(MonitoringConfig, raw)
    except ConfigurationError as ex:
        raise ConfigurationError(f"{path}: {ex}") from ex
    names = set()
    for s in config.schedules:
        if s.name.lower() in names:
            raise ConfigurationError(f"{path}: duplicate schedule name '{s.name}'")
        names.add(s.name.lower())
        if (s.every is None) == (s.at is None):
            raise ConfigurationError(f"{path}: schedule '{s.name}' needs exactly one of `every` (e.g. 6h) or `at` (HH:MM)")
        if s.every is not None and (s.interval is None or s.interval < timedelta(minutes=5)):
            raise ConfigurationError(f"{path}: schedule '{s.name}': every must look like 30m, 6h or 1d (at least 5 minutes)")
        if s.at is not None and not _AT.match(s.at.strip()):
            raise ConfigurationError(f"{path}: schedule '{s.name}': at must be HH:MM")
    for kind in config.alerts.notify:
        if kind not in ALERT_KINDS:
            raise ConfigurationError(f"{path}: alerts.notify: unknown kind '{kind}' (use {', '.join(ALERT_KINDS)})")
    if config.retention.exchanges_days < 1:
        raise ConfigurationError(f"{path}: retention.exchangesDays must be at least 1")
    return config


def next_due(schedule: Schedule, last_started: datetime | None, now: datetime) -> datetime:
    """When the schedule should run next (timezone-aware; `at` uses the local time zone). A schedule that never ran is
    due now (`every`) or at its next time of day (`at`), so starting the monitor does not trigger every daily run."""
    if schedule.every is not None:
        return now if last_started is None else last_started + schedule.interval
    hour, minute = (int(x) for x in schedule.at.strip().split(":"))
    local_now = now.astimezone()
    today = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    latest = today if today <= local_now else today - timedelta(days=1)  # the most recent scheduled time
    if last_started is None:
        return today if today > local_now else today + timedelta(days=1)
    return latest if last_started < latest else latest + timedelta(days=1)


def is_due(schedule: Schedule, last_started: datetime | None, now: datetime) -> bool:
    return schedule.enabled and next_due(schedule, last_started, now) <= now
