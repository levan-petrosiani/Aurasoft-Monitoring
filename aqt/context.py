import os
import threading
from datetime import datetime
from typing import Callable

from . import certificates
from .certificates import Certificate, Psd2CertificateInfo
from .httpclient import HttpClient
from .oauth import open_system_browser
from .profiles import BankProfile
from .scenarios import TemplateLibrary
from .signing import JwsSigner
from .variables import utc_now


class RunContext:
    """Shared state for one run against one bank."""

    def __init__(self, profile: BankProfile, templates: TemplateLibrary, qwac: Certificate | None,
                 qsealc: Certificate | None, http: HttpClient | None = None, now: Callable[[], datetime] | None = None):
        self.profile = profile
        self.templates = templates
        self.qwac = qwac
        self.qsealc = qsealc
        self.qwac_info = Psd2CertificateInfo.of(qwac) if qwac else None
        self.qsealc_info = Psd2CertificateInfo.of(qsealc) if qsealc else None
        self.signer = JwsSigner(qsealc) if qsealc else None
        self.now = now or utc_now

        self._owns_http = http is None
        self.http = http or HttpClient.for_profile(profile, qwac)

        # Opens the authorization URL for manual SCA: (url, cancel_event) -> None. Replaceable (--sca-auto, portal, tests).
        self.open_browser: Callable[[str, threading.Event], None] = open_system_browser
        # Messages the tester must see while a step waits for them (e.g. "log in at ...").
        self.notify: Callable[[str], None] = lambda message: None
        # Set to cancel the run (Ctrl+C, portal Cancel button).
        self.cancel = threading.Event()

    @property
    def tpp_id(self) -> str | None:
        """Our TPP id (organizationIdentifier of the QWAC)."""
        return self.qwac_info.organization_id if self.qwac_info else None

    @staticmethod
    def create(profile: BankProfile, templates: TemplateLibrary) -> "RunContext":
        """Loads the profile's certificates and creates the context. Missing certificates are allowed (preflight reports them)."""
        base_dir = os.path.dirname(profile.source_path) or os.getcwd()
        qwac = certificates.load(profile.tpp.qwac, base_dir, "tpp.qwac") if profile.tpp.qwac else None
        qsealc = certificates.load(profile.tpp.qsealc, base_dir, "tpp.qsealc") if profile.tpp.qsealc else None
        return RunContext(profile, templates, qwac, qsealc)

    def close(self):
        if self._owns_http:
            self.http.close()
        for cert in (self.qwac, self.qsealc):
            if cert is not None:
                cert.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
