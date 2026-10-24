import json
import os
from dataclasses import dataclass, fields

from ..binding import camel

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "mockbank.settings.json")


@dataclass
class MockBankOptions:
    # HTTPS port for XS2A, discovery and the token endpoint (mutual TLS).
    https_port: int = 7443
    # Plain HTTP port for the browser-facing pages: the OAuth authorize page and the dashboard.
    http_port: int = 7080
    # When the OAuth authorize request is approved without showing the login page:
    # always, headless (only for ApiTestTool's --sca-auto, real browsers see the page) or never.
    auto_approve: str = "headless"
    # Reject XS2A requests without a valid X-Jws-Signature.
    verify_signatures: bool = True
    # Sign XS2A responses (X-Jws-Signature + Digest) with the bank's generated QSealC.
    sign_responses: bool = True
    # The bank's own PSD2 id, used in its generated certificates.
    bank_organization_id: str = "PSDGE-NBG-MOCKBANK"
    # Highest accepted frequencyPerDay, like AspspSettings.MaxFrequencyPerDay.
    max_frequency_per_day: int = 4
    # Longest consent in days (GE-XS2A 9.1.1.10): a later validTo is rejected with PERIOD_INVALID, except 9999-12-31,
    # which means "as long as allowed" and is shortened to this many days.
    max_consent_days: int = 90
    access_token_lifetime_seconds: int = 900

    @property
    def https_base_url(self) -> str:
        return f"https://localhost:{self.https_port}"

    @property
    def http_base_url(self) -> str:
        return f"http://localhost:{self.http_port}"

    @staticmethod
    def load(overrides: dict | None = None) -> "MockBankOptions":
        """
        mockbank.settings.json ("MockBank" section), then environment variables such as MockBank__AutoApprove=never,
        then overrides. Names match case-insensitively with or without underscores (HttpsPort, https_port).
        """
        values: dict = {}
        if os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                values.update(json.load(f).get("MockBank", {}))
        for f in fields(MockBankOptions):
            pascal = camel(f.name)[0].upper() + camel(f.name)[1:]
            if f"MockBank__{pascal}" in os.environ:
                values[f.name] = os.environ[f"MockBank__{pascal}"]
        values.update(overrides or {})

        options = MockBankOptions()
        by_name = {f.name.replace("_", ""): f for f in fields(MockBankOptions)}
        for key, value in values.items():
            f = by_name.get(key.replace("_", "").lower())
            if f is None:
                continue
            if f.type is int:
                value = int(value)
            elif f.type is bool and isinstance(value, str):
                value = value.strip().lower() == "true"
            setattr(options, f.name, value)
        return options
