"""Everything the mock bank remembers, in memory. Restarting MockBank starts from scratch.

Authorisations, codes and tokens carry a `consent_id`: the consent (scope AIS:<id>) or the payment (scope PIS:<id>)
they belong to; the scope says which."""

import secrets
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_today() -> date:
    return utc_now().date()


@dataclass
class Consent:
    id: str
    tpp_id: str
    consent_type: str
    access: dict
    recurring_indicator: bool
    valid_to: date
    frequency_per_day: int
    redirect_uri: str | None = None
    status: str = "received"
    created_utc: datetime = field(default_factory=utc_now)
    last_action_date: date = field(default_factory=utc_today)

    @property
    def rights(self) -> list[str]:
        """All rights granted anywhere in the access object (payments, cards, savings, ...), in order, unique."""
        result = []
        for value in self.access.values():
            if not isinstance(value, list):
                continue
            for entry in value:
                if isinstance(entry, dict) and isinstance(entry.get("rights"), list):
                    for right in entry["rights"]:
                        text = right if isinstance(right, str) else ""
                        if text not in result:
                            result.append(text)
        return result


@dataclass
class Payment:
    """A payment initiation (Georgian XS2A 8): /v1/{service}/{product}/{id}."""
    id: str
    tpp_id: str
    service: str                 # payments
    product: str                 # aspsp | domestic | foreign
    body: dict
    amount: Decimal
    currency: str
    debtor_iban: str | None
    redirect_uri: str | None = None
    # ACTC on receipt (never RCVD, GE-XS2A 8.10); after SCA: aspsp -> ACCC, domestic/foreign -> ACSP, then ACSC.
    status: str = "ACTC"
    funds_available: bool | None = None
    authorised_utc: datetime | None = None
    psu_authenticated: bool = False
    created_utc: datetime = field(default_factory=utc_now)


@dataclass
class Authorisation:
    id: str
    # The consent (AIS) or payment (PIS) this authorisation is for.
    consent_id: str
    sca_status: str = "received"


@dataclass
class AuthorizationCode:
    code: str
    client_id: str
    redirect_uri: str
    scope: str
    consent_id: str
    code_challenge: str
    expires_utc: datetime
    used: bool = False


@dataclass(frozen=True)
class AccessToken:
    token: str
    client_id: str
    consent_id: str
    scope: str
    expires_utc: datetime


@dataclass(frozen=True)
class RefreshToken:
    token: str
    client_id: str
    consent_id: str
    scope: str


@dataclass(frozen=True)
class MockAccount:
    resource_id: str
    iban: str
    currency: str
    name: str
    product: str
    balance: Decimal


class MockState:
    def __init__(self):
        self.lock = threading.RLock()
        self.consents: dict[str, Consent] = {}
        self.payments: dict[str, Payment] = {}
        self.authorisations: dict[str, Authorisation] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}

        # The sandbox PSU's accounts (every consent sees the same test customer).
        self.accounts = [
            MockAccount("3dc3d5b3-7023-4848-9853-f5400a64e80f", "GE63MK0000000000000001", "GEL", "Main account", "Current account", Decimal("1540.25")),
            MockAccount("3dc3d5b3-7023-4848-9853-f5400a64e81f", "GE36MK0000000000000002", "USD", "Savings in USD", "Current account", Decimal("820.00")),
        ]

    @staticmethod
    def new_token() -> str:
        return secrets.token_hex(24)

    def revoke_tokens_for(self, consent_id: str):
        with self.lock:
            for t in [t for t in self.access_tokens.values() if t.consent_id == consent_id]:
                self.access_tokens.pop(t.token, None)
            for t in [t for t in self.refresh_tokens.values() if t.consent_id == consent_id]:
                self.refresh_tokens.pop(t.token, None)
