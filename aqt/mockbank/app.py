"""MockBank request handling: XS2A security (mTLS, X-Request-ID, JWS both ways), consents, accounts, OAuth2."""

import hashlib
import html
import ipaddress
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, quote, urlsplit

from cryptography import x509

from ..certificates import Certificate, organization_id
from ..signing import JwsSigner, b64url_encode, verify_response
from .options import MockBankOptions
from .state import (AccessToken, Authorisation, AuthorizationCode, Consent, MockAccount, MockState, Payment, RefreshToken,
                    utc_now, utc_today)

HEADLESS_USER_AGENT = "ApiTestTool-HeadlessSca"
_SUPPORTED_TYPES = {"accountList", "aspspManaged", "detailed"}


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: list[tuple[str, str]]
    body: bytes
    client_cert: x509.Certificate | None = None
    # Set by the XS2A middleware: the TPP id from the client certificate.
    tpp_id: str | None = None

    def header(self, name: str) -> str:
        """The header value, or "" when it is absent (like ASP.NET's Headers[name].ToString())."""
        values = [v for k, v in self.headers if k.lower() == name.lower()]
        return ",".join(values)

    def headers_dict(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for k, v in self.headers:
            result[k] = f"{result[k]},{v}" if k in result else v
        return result

    def q(self, name: str) -> str | None:
        values = self.query.get(name)
        return values[0] if values else None

    def form(self) -> dict[str, list[str]]:
        return parse_qs(self.body.decode("utf-8", errors="replace"), keep_blank_values=True)


@dataclass
class Response:
    status: int = 200
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""

    def set_header(self, name: str, value: str):
        self.headers = [(k, v) for k, v in self.headers if k.lower() != name.lower()] + [(name, value)]


def json_response(obj, status: int = 200, content_type: str = "application/json; charset=utf-8") -> Response:
    return Response(status, [("Content-Type", content_type)], json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def html_response(text: str, status: int = 200) -> Response:
    return Response(status, [("Content-Type", "text/html; charset=utf-8")], text.encode("utf-8"))


def redirect(url: str) -> Response:
    return Response(302, [("Location", url)])


# ---- Problems: errors in the Georgian XS2A format (application/problem+json with a Berlin Group code) ----------

def xs2a_problem(status: int, code: str, detail: str) -> Response:
    return json_response({
        "type": f"https://berlin-group.org/problems/{code.lower().replace('_', '-')}",
        "title": code,
        "status": status,
        "detail": detail,
        "code": code,
    }, status, "application/problem+json")


def format_error(detail: str) -> Response:
    return xs2a_problem(400, "FORMAT_ERROR", detail)


def parameter_not_supported(detail: str) -> Response:
    return xs2a_problem(400, "PARAMETER_NOT_SUPPORTED", detail)


def consent_unknown() -> Response:
    return xs2a_problem(403, "CONSENT_UNKNOWN", "The consent does not exist or belongs to another TPP")


def token_unknown() -> Response:
    return xs2a_problem(401, "TOKEN_UNKNOWN", "A valid Bearer access token is required")


def oauth_problem(error: str, description: str, status: int = 400) -> Response:
    """OAuth2 (RFC 6749) error for the token endpoint."""
    return json_response({"error": error, "error_description": description}, status)


def link(href: str) -> dict:
    return {"href": href}


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _is_guid(value: str | None) -> bool:
    if not value:
        return False
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _escape(value: str) -> str:
    return quote(value, safe="-_.~")


def _is_absolute(url: str | None) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    return bool(parts.scheme and (parts.netloc or parts.scheme == "file"))


def _amount(currency: str, amount: Decimal) -> dict:
    return {"currency": currency, "amount": f"{amount:.2f}"}


PAYMENT_SERVICES = ("payments",)
PAYMENT_PRODUCTS = ("aspsp", "domestic", "foreign")
# GE-XS2A 8.2.1, table 4: fields marked "-" (never) for every product; sending one is a FORMAT_ERROR.
_NEVER = ("debtorId", "currencyOfTransfer", "exchangeRateInformation", "creditorId", "creditorNameAndAddress",
          "ultimateCreditor", "ultimateCreditorIdentification", "purposeCode", "serviceLevel",
          "remittanceInformationStructured", "remittanceInformationStructuredArray", "requestedExecutionTime")
# Per product (only the columns that are unambiguous in the table): never / mandatory.
_NEVER_FOR = {"aspsp": ("creditorName", "creditorAgentName", "chargeBearer", "additionalInformation", "instructionPriority"),
              "foreign": ("requestedExecutionDate",)}
_MANDATORY_FOR = {"foreign": ("creditorAgent", "creditorName")}
_AMOUNT = re.compile(r"^-?[0-9]{1,14}(\.[0-9]{1,3})?$")


def valid_iban(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", value):
        return False
    rearranged = value[4:] + value[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def validate_payment(product: str, body: dict) -> str | None:
    """The FORMAT_ERROR checks of GE-XS2A 8.2.1 that MockBank enforces; None when the body is acceptable."""
    for name in _NEVER + _NEVER_FOR.get(product, ()):
        if name in body:
            return f"'{name}' must not be sent for {product} payments (GE-XS2A 8.2.1)"
    for name in ("instructedAmount", "creditorAccount") + _MANDATORY_FOR.get(product, ()):
        if body.get(name) in (None, "", {}):
            return f"'{name}' is required for {product} payments"
    amount = body["instructedAmount"]
    if not isinstance(amount, dict) or not isinstance(amount.get("currency"), str) or not re.fullmatch(r"[A-Z]{3}", amount["currency"]):
        return "'instructedAmount.currency' must be an ISO 4217 code"
    if not isinstance(amount.get("amount"), str) or not _AMOUNT.match(amount["amount"]) or Decimal(amount["amount"]) <= 0:
        return "'instructedAmount.amount' must be a positive decimal string like \"10.50\""
    for name in ("creditorAccount", "debtorAccount"):
        account = body.get(name)
        if account is None and name == "debtorAccount":
            continue
        if not isinstance(account, dict) or not valid_iban(account.get("iban")):
            return f"'{name}.iban' is not a valid IBAN"
    remittance = body.get("remittanceInformationUnstructured")
    if remittance is not None and (not isinstance(remittance, str) or len(remittance) > 140):
        return "'remittanceInformationUnstructured' must be text of at most 140 characters"
    return None


class MockBankApp:
    def __init__(self, options: MockBankOptions, server_certificate: Certificate, qsealc: Certificate):
        self.options = options
        self.server_certificate = server_certificate
        self.state = MockState()
        self._signer = JwsSigner(qsealc)
        self._routes = [
            ("POST", r"/v2/consents/account-access", self.create_consent),
            ("GET", r"/v2/consents/account-access/(?P<consent_id>[^/]+)/status", self.get_consent_status),
            ("GET", r"/v2/consents/account-access/(?P<consent_id>[^/]+)", self.get_consent),
            ("DELETE", r"/v2/consents/account-access/(?P<consent_id>[^/]+)", self.delete_consent),
            ("POST", r"/v2/consents/(?P<consent_id>[^/]+)/authorisations", self.start_authorisation),
            ("GET", r"/v2/consents/(?P<consent_id>[^/]+)/authorisations/(?P<authorisation_id>[^/]+)", self.get_sca_status),
            ("GET", r"/v2/accounts", self.list_accounts),
            ("GET", r"/v2/accounts/(?P<account_id>[^/]+)", self.get_account),
            ("GET", r"/v2/accounts/(?P<account_id>[^/]+)/balances", self.get_balances),
            ("GET", r"/v2/accounts/(?P<account_id>[^/]+)/transactions", self.get_transactions),
            ("POST", r"/v1/(?P<service>[^/]+)/(?P<product>[^/]+)", self.initiate_payment),
            ("GET", r"/v1/(?P<service>[^/]+)/(?P<product>[^/]+)/(?P<payment_id>[^/]+)/status", self.get_payment_status),
            ("POST", r"/v1/(?P<service>[^/]+)/(?P<product>[^/]+)/(?P<payment_id>[^/]+)/authorisations", self.start_payment_authorisation),
            ("GET", r"/v1/(?P<service>[^/]+)/(?P<product>[^/]+)/(?P<payment_id>[^/]+)/authorisations/(?P<authorisation_id>[^/]+)",
             self.get_payment_sca_status),
            ("GET", r"/v1/(?P<service>[^/]+)/(?P<product>[^/]+)/(?P<payment_id>[^/]+)", self.get_payment),
            ("DELETE", r"/v1/(?P<service>[^/]+)/(?P<product>[^/]+)/(?P<payment_id>[^/]+)", self.cancel_payment),
            ("GET", r"/\.well-known/oauth-authorization-server", self.discovery),
            ("GET", r"/\.well-known/openid-configuration", self.discovery),
            ("GET", r"/oauth/authorize", self.authorize),
            ("POST", r"/oauth/authorize", self.decide),
            ("POST", r"/oauth/token", self.token),
            ("GET", r"/", self.dashboard),
        ]
        self._compiled = [(m, re.compile(f"^{p}/?$" if p != "/" else "^/$", re.I), h) for m, p, h in self._routes]

    # ---- pipeline ------------------------------------------------------------------------------------------------

    def handle(self, request: Request) -> Response:
        path = request.path
        lower = path.lower()
        if lower in ("/v1", "/v2") or lower.startswith(("/v1/", "/v2/")):
            return self._xs2a(request)
        return self._route(request)

    def _route(self, request: Request) -> Response:
        for method, pattern, handler in self._compiled:
            m = pattern.match(request.path)
            if m and method == request.method.upper():
                return handler(request, **m.groupdict())
        return Response(404)

    def _xs2a(self, request: Request) -> Response:
        """
        What a real ASPSP does around every XS2A call: requires the TPP's client certificate (mTLS) and a GUID
        X-Request-ID, verifies the X-Jws-Signature/Digest, echoes X-Request-ID, and signs the response.
        """
        request_id = request.header("X-Request-ID")
        response = self._check_request(request, request_id) or self._route(request)

        if request_id:
            response.set_header("X-Request-ID", request_id)

        if self.options.sign_responses:
            signature = self._signer.sign([("X-Request-ID", request_id)], response.body, utc_now())
            response.set_header("Digest", signature.digest)
            response.set_header("X-Jws-Signature", signature.jws_signature)

        return response

    def _check_request(self, request: Request, request_id: str) -> Response | None:
        if request.client_cert is None:
            return xs2a_problem(401, "CERTIFICATE_MISSING", "No client certificate (QWAC) was presented")

        tpp_id = organization_id(request.client_cert.subject)
        if tpp_id is None:
            return xs2a_problem(401, "CERTIFICATE_INVALID", "The client certificate has no organizationIdentifier (TPP id)")
        request.tpp_id = tpp_id

        if not _is_guid(request_id) or uuid.UUID(request_id).int == 0:
            return format_error("X-Request-ID header is missing or not a UUID")

        if not self.options.verify_signatures:
            return None

        verification = verify_response(request.headers_dict(), request.body, 0, utc_now())

        if not verification.is_present:
            return xs2a_problem(401, "SIGNATURE_MISSING", "X-Jws-Signature header is missing")

        if any("Digest" in e for e in verification.errors) and all("Signature value" not in e for e in verification.errors):
            return format_error("Digest: " + "; ".join(verification.errors))

        if not verification.is_valid:
            return xs2a_problem(401, "SIGNATURE_INVALID", "; ".join(verification.errors))

        if verification.signer_organization_id != tpp_id:
            return xs2a_problem(401, "CERTIFICATE_INVALID",
                                f"QSealC belongs to '{verification.signer_organization_id}', QWAC to '{tpp_id}'")

        return None

    # ---- consents (shaped after XSA2 ConsentsController, Georgian XS2A v2) ---------------------------------------

    def create_consent(self, request: Request) -> Response:
        ip = request.header("PSU-IP-Address")
        if not ip:
            return format_error("PSU-IP-Address header is required")
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return format_error(f"PSU-IP-Address '{ip}' is not an IP address")

        try:
            body = json.loads(request.body)
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except ValueError as ex:
            return format_error(f"Request body is not a valid JSON object: {ex}")

        access = body.get("access")
        if not isinstance(access, dict):
            return format_error("'access' is required")

        consent_type = _text(body.get("consentType"))
        if consent_type == "global":
            return parameter_not_supported("consentType 'global' (all PSD2 accounts) is not supported")
        if consent_type is None or consent_type not in _SUPPORTED_TYPES:
            return format_error(f"consentType '{consent_type or ''}' is not valid")

        rights = [_text(r) for value in access.values() if isinstance(value, list)
                  for entry in value if isinstance(entry, dict)
                  for r in (entry.get("rights") if isinstance(entry.get("rights"), list) else [])]
        if "trustedBeneficiaries" in rights:
            return parameter_not_supported("Access to trusted beneficiaries is not supported")
        if not rights:
            return format_error("At least one access right is required")

        recurring = body.get("recurringIndicator")
        if not isinstance(recurring, bool):
            return format_error("'recurringIndicator' must be true or false")

        valid_to_text = _text(body.get("validTo"))
        try:
            if valid_to_text is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valid_to_text):
                raise ValueError()
            valid_to = date.fromisoformat(valid_to_text)
        except ValueError:
            return format_error("'validTo' must be a date (yyyy-MM-dd)")
        if valid_to < utc_today():
            return format_error("'validTo' is in the past")
        # GE-XS2A 9.1.1.10: at most max_consent_days from the request (PERIOD_INVALID); 9999-12-31 is shortened to the maximum.
        latest = utc_today() + timedelta(days=self.options.max_consent_days)
        if valid_to == date(9999, 12, 31):
            valid_to = latest
        elif valid_to > latest:
            return xs2a_problem(400, "PERIOD_INVALID", f"'validTo' is more than {self.options.max_consent_days} days from today")

        frequency = body.get("frequencyPerDay")
        if isinstance(frequency, bool) or not isinstance(frequency, int) or frequency < 1:
            return format_error("'frequencyPerDay' must be a positive number")
        if frequency > self.options.max_frequency_per_day:
            return format_error(f"'frequencyPerDay' {frequency} exceeds the maximum {self.options.max_frequency_per_day}")

        consent = Consent(id=str(uuid.uuid4()), tpp_id=request.tpp_id, consent_type=consent_type, access=access,
                          recurring_indicator=recurring, valid_to=valid_to, frequency_per_day=frequency,
                          redirect_uri=self._redirect_uri(request))
        with self.state.lock:
            self.state.consents[consent.id] = consent

        self_link = f"/v2/consents/account-access/{consent.id}"
        response = json_response({
            "consentStatus": consent.status,
            "consentId": consent.id,
            "_links": {
                "self": link(self_link),
                "status": link(f"{self_link}/status"),
                "startAuthorisation": link(f"/v2/consents/{consent.id}/authorisations"),
            },
        }, 201)
        response.set_header("Location", self_link)
        response.set_header("ASPSP-SCA-Approach", "REDIRECT")
        return response

    def get_consent_status(self, request: Request, consent_id: str) -> Response:
        consent = self._own_consent(request, consent_id)
        if consent is None:
            return consent_unknown()
        return json_response({"consentStatus": consent.status})

    def get_consent(self, request: Request, consent_id: str) -> Response:
        # Reading consent details needs the PSU's token; checked before existence, like the real API.
        token = self._token(request)
        if token is None:
            return token_unknown()

        consent = self._own_consent(request, consent_id)
        if consent is None or token.consent_id != consent_id:
            return consent_unknown()

        return json_response({
            "access": consent.access,
            "consentType": consent.consent_type,
            "recurringIndicator": consent.recurring_indicator,
            "validTo": consent.valid_to.isoformat(),
            "frequencyPerDay": consent.frequency_per_day,
            "lastActionDate": consent.last_action_date.isoformat(),
            "consentStatus": consent.status,
        })

    def delete_consent(self, request: Request, consent_id: str) -> Response:
        consent = self._own_consent(request, consent_id)
        if consent is None:
            return consent_unknown()

        consent.status = "terminatedByTpp"
        self.state.revoke_tokens_for(consent_id)
        return Response(204)

    def start_authorisation(self, request: Request, consent_id: str) -> Response:
        consent = self._own_consent(request, consent_id)
        if consent is None:
            return consent_unknown()

        if consent.status != "received":
            return xs2a_problem(409, "STATUS_INVALID", f"Consent is '{consent.status}', authorisation needs 'received'")

        redirect_uri = self._redirect_uri(request)
        if consent.redirect_uri is not None and redirect_uri is not None and redirect_uri != consent.redirect_uri:
            return format_error("Client-Redirect-URI differs from the one sent when the consent was created")

        authorisation = Authorisation(id=str(uuid.uuid4()), consent_id=consent_id)
        with self.state.lock:
            self.state.authorisations[authorisation.id] = authorisation

        self_link = f"/v2/consents/{consent_id}/authorisations/{authorisation.id}"
        response = json_response({
            "authorisationId": authorisation.id,
            "scaStatus": authorisation.sca_status,
            "_links": {
                "scaOAuth": link(f"{self.options.https_base_url}/.well-known/oauth-authorization-server"),
                "scaStatus": link(self_link),
            },
        }, 201)
        response.set_header("Location", self_link)
        return response

    def get_sca_status(self, request: Request, consent_id: str, authorisation_id: str) -> Response:
        authorisation = self.state.authorisations.get(authorisation_id)
        if self._own_consent(request, consent_id) is None or authorisation is None or authorisation.consent_id != consent_id:
            return xs2a_problem(403, "RESOURCE_UNKNOWN", "Authorisation not found")
        return json_response({"scaStatus": authorisation.sca_status})

    def _own_consent(self, request: Request, consent_id: str) -> Consent | None:
        consent = self.state.consents.get(consent_id)
        return consent if consent is not None and consent.tpp_id == request.tpp_id else None

    @staticmethod
    def _redirect_uri(request: Request) -> str | None:
        return request.header("Client-Redirect-URI") or request.header("TPP-Redirect-URI") or None

    def _token(self, request: Request) -> AccessToken | None:
        """The valid, unexpired access token from the Authorization header, or None."""
        header = request.header("Authorization")
        if not header.lower().startswith("bearer "):
            return None
        token = self.state.access_tokens.get(header[7:].strip())
        return token if token is not None and token.expires_utc > utc_now() else None

    # ---- payments (Georgian XS2A 8: /v1/{payment-service}/{payment-product}) -----------------------------------------

    def initiate_payment(self, request: Request, service: str, product: str) -> Response:
        if service not in PAYMENT_SERVICES:
            return xs2a_problem(404, "SERVICE_INVALID" if service in ("bulk-payments", "periodic-payments") else "RESOURCE_UNKNOWN",
                                f"Payment service '{service}' is not supported by MockBank (supported: {', '.join(PAYMENT_SERVICES)})")
        if product not in PAYMENT_PRODUCTS:
            return xs2a_problem(404, "PRODUCT_UNKNOWN", f"Payment product '{product}' is not supported (aspsp, domestic, foreign)")
        if not request.header("PSU-IP-Address"):
            return format_error("PSU-IP-Address header is required")
        if request.header("Authorization") and self._token(request) is None:
            return token_unknown()  # GE-XS2A 8.3: a token is optional; if one is sent it must be valid
        try:
            body = json.loads(request.body or b"")
        except ValueError:
            return format_error("The body is not valid JSON")
        if not isinstance(body, dict):
            return format_error("The body must be a JSON object")

        problem = validate_payment(product, body)
        if problem is not None:
            return format_error(problem)

        amount = Decimal(body["instructedAmount"]["amount"])
        currency = body["instructedAmount"]["currency"]
        debtor_iban = (body.get("debtorAccount") or {}).get("iban")
        debtor = next((a for a in self.state.accounts if debtor_iban and a.iban == debtor_iban), None)
        funds = None if debtor is None else (debtor.currency == currency and debtor.balance >= amount)
        # GE-XS2A 8.3: with TPP-Rejection-NoFunds-Preferred true or absent, insufficient funds are rejected at once.
        if funds is False and request.header("TPP-Rejection-NoFunds-Preferred").lower() != "false":
            return xs2a_problem(400, "PAYMENT_FAILED", f"Insufficient funds on {debtor_iban} for {amount} {currency}")

        payment = Payment(id=str(uuid.uuid4()), tpp_id=request.tpp_id, service=service, product=product, body=body,
                          amount=amount, currency=currency, debtor_iban=debtor_iban, redirect_uri=self._redirect_uri(request),
                          funds_available=funds)
        if funds is False:
            payment.status = "ACCP"  # waits for funds (TPP-Rejection-NoFunds-Preferred: false)
        with self.state.lock:
            self.state.payments[payment.id] = payment

        self_link = f"/v1/{service}/{product}/{payment.id}"
        response = json_response({
            "transactionStatus": payment.status,
            "paymentId": payment.id,
            "_links": {
                "self": link(self_link),
                "status": link(f"{self_link}/status"),
                "startAuthorisation": link(f"{self_link}/authorisations"),
            },
        }, 201)
        response.set_header("Location", self_link)
        response.set_header("ASPSP-SCA-Approach", "REDIRECT")
        return response

    def _payment(self, request: Request, service: str, product: str, payment_id: str) -> tuple[Payment | None, Response | None]:
        """The TPP's payment at this path; once the PSU has authenticated for it, a token for it is required (GE-XS2A 8.5-8.7)."""
        payment = self.state.payments.get(payment_id)
        if payment is None or payment.tpp_id != request.tpp_id or payment.service != service or payment.product != product:
            return None, xs2a_problem(403, "RESOURCE_UNKNOWN", "Payment not found")
        token = self._token(request)
        if request.header("Authorization") and token is None:
            return None, token_unknown()
        if token is not None and token.consent_id != payment.id:
            return None, xs2a_problem(401, "TOKEN_INVALID", "The access token was not issued for this payment")
        if payment.psu_authenticated and token is None:
            return None, xs2a_problem(401, "TOKEN_INVALID", "The PSU authenticated for this payment: the Authorization header is required")
        self._settle(payment)
        return payment, None

    def _settle(self, payment: Payment):
        if payment.status == "ACSP" and payment.authorised_utc is not None and \
                (utc_now() - payment.authorised_utc).total_seconds() >= self.options.settlement_seconds:
            payment.status = "ACSC"

    def get_payment_status(self, request: Request, service: str, product: str, payment_id: str) -> Response:
        payment, error = self._payment(request, service, product, payment_id)
        if error is not None:
            return error
        body = {"transactionStatus": payment.status}
        if payment.status in ("ACTC", "ACWC", "ACCP"):
            # GE-XS2A 8.5: fundsAvailable is required for these statuses.
            body["fundsAvailable"] = payment.funds_available if payment.funds_available is not None else True
        return json_response(body)

    def get_payment(self, request: Request, service: str, product: str, payment_id: str) -> Response:
        payment, error = self._payment(request, service, product, payment_id)
        if error is not None:
            return error
        return json_response({**payment.body, "transactionStatus": payment.status})

    def cancel_payment(self, request: Request, service: str, product: str, payment_id: str) -> Response:
        payment, error = self._payment(request, service, product, payment_id)
        if error is not None:
            return error
        if payment.status not in ("ACTC", "ACCP"):
            # GE-XS2A 8.7: 405 when the status no longer allows cancellation.
            return xs2a_problem(405, "CANCELLATION_INVALID", f"The payment is '{payment.status}' and can no longer be cancelled")
        payment.status = "CANC"
        return Response(204)

    def start_payment_authorisation(self, request: Request, service: str, product: str, payment_id: str) -> Response:
        payment, error = self._payment(request, service, product, payment_id)
        if error is not None:
            return error
        if payment.status not in ("ACTC", "ACCP"):
            return xs2a_problem(409, "STATUS_INVALID", f"Payment is '{payment.status}', authorisation needs ACTC")
        redirect_uri = self._redirect_uri(request)
        if payment.redirect_uri is not None and redirect_uri is not None and redirect_uri != payment.redirect_uri:
            return format_error("Client-Redirect-URI differs from the one sent when the payment was initiated")

        authorisation = Authorisation(id=str(uuid.uuid4()), consent_id=payment.id)
        with self.state.lock:
            self.state.authorisations[authorisation.id] = authorisation
        self_link = f"/v1/{service}/{product}/{payment.id}/authorisations/{authorisation.id}"
        response = json_response({
            "authorisationId": authorisation.id,
            "scaStatus": authorisation.sca_status,
            "_links": {
                "scaOAuth": link(f"{self.options.https_base_url}/.well-known/oauth-authorization-server"),
                "scaStatus": link(self_link),
            },
        }, 201)
        response.set_header("Location", self_link)
        return response

    def get_payment_sca_status(self, request: Request, service: str, product: str, payment_id: str, authorisation_id: str) -> Response:
        payment, error = self._payment(request, service, product, payment_id)
        if error is not None:
            return error
        authorisation = self.state.authorisations.get(authorisation_id)
        if authorisation is None or authorisation.consent_id != payment.id:
            return xs2a_problem(403, "RESOURCE_UNKNOWN", "Authorisation not found")
        return json_response({"scaStatus": authorisation.sca_status})

    def _authorise_payment(self, payment: Payment):
        """The PSU confirmed the payment (token issued): intra-bank is booked at once, interbank settles later."""
        payment.psu_authenticated = True
        if payment.status in ("ACTC", "ACCP"):
            payment.authorised_utc = utc_now()
            payment.status = "ACCC" if payment.product == "aspsp" else "ACSP"

    # ---- accounts (XSA2 AccountsController, fixed sandbox data) ----------------------------------------------------

    def _authorize(self, request: Request, right: str | None, handler) -> Response:
        """Common AIS checks: Consent-ID header, token for that consent, consent valid, right granted."""
        consent_id = request.header("Consent-ID")
        if not consent_id:
            return format_error("Consent-ID header is required")

        token = self._token(request)
        if token is None:
            return token_unknown()
        if token.consent_id != consent_id:
            return xs2a_problem(401, "TOKEN_INVALID", "The access token was not issued for this consent")

        consent = self._own_consent(request, consent_id)
        if consent is None:
            return consent_unknown()
        if consent.status != "valid":
            return xs2a_problem(401, "CONSENT_INVALID", f"Consent is '{consent.status}'")
        if right is not None and consent.consent_type != "aspspManaged" and right not in consent.rights:
            return xs2a_problem(401, "CONSENT_INVALID", f"The consent does not grant '{right}'")

        consent.last_action_date = utc_today()
        return handler(consent)

    def _find(self, account_id: str) -> MockAccount | None:
        return next((a for a in self.state.accounts if a.resource_id == account_id), None)

    @staticmethod
    def _bool_query(request: Request, name: str) -> bool:
        return (request.q(name) or "").lower() == "true"

    def list_accounts(self, request: Request) -> Response:
        with_balance = self._bool_query(request, "withBalance")
        return self._authorize(request, None, lambda consent: json_response({
            "accounts": [self._account_json(a, with_balance and "balances" in consent.rights) for a in self.state.accounts],
        }))

    def get_account(self, request: Request, account_id: str) -> Response:
        with_balance = self._bool_query(request, "withBalance")

        def handler(consent):
            a = self._find(account_id)
            if a is None:
                return xs2a_problem(403, "RESOURCE_UNKNOWN", f"Account '{account_id}' is not covered by the consent")
            return json_response({"account": self._account_json(a, with_balance and "balances" in consent.rights)})

        return self._authorize(request, None, handler)

    def get_balances(self, request: Request, account_id: str) -> Response:
        def handler(_):
            a = self._find(account_id)
            if a is None:
                return xs2a_problem(403, "RESOURCE_UNKNOWN", f"Account '{account_id}' is not covered by the consent")
            return json_response({"account": {"iban": a.iban, "currency": a.currency}, "balances": self._balances(a)})

        return self._authorize(request, "balances", handler)

    def get_transactions(self, request: Request, account_id: str) -> Response:
        booking_status = request.q("bookingStatus")

        def handler(_):
            if booking_status not in ("booked", "pending", "both"):
                return format_error("Query parameter 'bookingStatus' must be booked, pending or both")
            a = self._find(account_id)
            if a is None:
                return xs2a_problem(403, "RESOURCE_UNKNOWN", f"Account '{account_id}' is not covered by the consent")
            return json_response({"account": {"iban": a.iban, "currency": a.currency},
                                  "transactions": self._transactions(a, booking_status)})

        return self._authorize(request, "transactions", handler)

    def _account_json(self, a: MockAccount, with_balance: bool) -> dict:
        result = {
            "resourceId": a.resource_id,
            "iban": a.iban,
            "currency": a.currency,
            "name": a.name,
            "product": a.product,
            "cashAccountType": "CACC",
            "status": "enabled",
            "_links": {
                "balances": link(f"/v2/accounts/{a.resource_id}/balances"),
                "transactions": link(f"/v2/accounts/{a.resource_id}/transactions"),
            },
        }
        if with_balance:
            result["balances"] = self._balances(a)
        return result

    @staticmethod
    def _balances(a: MockAccount) -> list:
        today = utc_today().isoformat()
        return [
            {"balanceAmount": _amount(a.currency, a.balance), "balanceType": "interimAvailable", "referenceDate": today},
            {"balanceAmount": _amount(a.currency, a.balance + Decimal(100)), "balanceType": "closingBooked", "referenceDate": today},
        ]

    @staticmethod
    def _transactions(a: MockAccount, booking_status: str) -> dict:
        result = {}
        if booking_status in ("booked", "both"):
            result["booked"] = [{
                "transactionId": f"{a.resource_id[:8]}-B{i}",
                "bookingDate": (utc_now() - timedelta(days=i * 3)).date().isoformat(),
                "valueDate": (utc_now() - timedelta(days=i * 3)).date().isoformat(),
                "transactionAmount": _amount(a.currency, Decimal("-12.5") * i),
                "creditorName": f"Shop {i}",
                "remittanceInformationUnstructured": f"Card payment {i}",
            } for i in range(1, 4)]

        if booking_status in ("pending", "both"):
            result["pending"] = [{
                "transactionId": f"{a.resource_id[:8]}-P1",
                "transactionAmount": _amount(a.currency, Decimal(-5)),
                "creditorName": "Coffee",
            }]

        result["_links"] = {"account": link(f"/v2/accounts/{a.resource_id}")}
        return result

    # ---- OAuth2 authorization server (discovery, authorize, token with PKCE S256, refresh) ------------------------

    def discovery(self, request: Request) -> Response:
        o = self.options
        return json_response({
            "issuer": o.https_base_url,
            "authorization_endpoint": f"{o.http_base_url}/oauth/authorize",
            "token_endpoint": f"{o.https_base_url}/oauth/token",
            "registration_endpoint": f"{o.https_base_url}/oauth/register",
            "scopes_supported": ["AIS", "PIS"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["tls_client_auth"],
            "ui_locales_supported": ["ka", "en"],
            "code_challenge_methods_supported": ["S256"],
            "mtls_endpoint_aliases": {"token_endpoint": f"{o.https_base_url}/oauth/token"},
            "tls_client_certificate_bound_access_tokens": True,
        })

    @staticmethod
    def _authorize_request(values: dict[str, list[str]]) -> dict:
        names = ("response_type", "client_id", "redirect_uri", "scope", "state", "code_challenge", "code_challenge_method")
        return {n: (values.get(n) or [None])[0] for n in names}

    def authorize(self, request: Request) -> Response:
        r = self._authorize_request(request.query)

        if not _is_absolute(r["redirect_uri"]):
            return self._error_page("redirect_uri is missing or invalid")

        consent, error = self._validate_authorize(r)
        if error is not None:
            return self._redirect_with_error(r, "invalid_request", error)

        if self.options.auto_approve == "always":
            auto = True
        elif self.options.auto_approve == "never":
            auto = False
        else:
            auto = HEADLESS_USER_AGENT in request.header("User-Agent")

        return self._approve(r, consent) if auto else self._login_page(r, consent)

    def decide(self, request: Request) -> Response:
        form = request.form()
        r = self._authorize_request(form)

        consent, error = self._validate_authorize(r)
        if error is not None:
            return self._redirect_with_error(r, "invalid_request", error)

        if (form.get("decision") or [None])[0] != "approve":
            if isinstance(consent, Payment):
                consent.status = "RJCT"
                return self._redirect_with_error(r, "access_denied", "The PSU rejected the payment")
            consent.status = "rejected"
            return self._redirect_with_error(r, "access_denied", "The PSU rejected the consent")

        return self._approve(r, consent)

    def _validate_authorize(self, r: dict):
        if r["response_type"] != "code":
            return None, "response_type must be 'code'"
        if not r["client_id"]:
            return None, "client_id is required"
        if not r["code_challenge"] or r["code_challenge_method"] != "S256":
            return None, "PKCE with code_challenge_method=S256 is required"

        kind, resource_id = self._resource_from_scope(r["scope"])
        if resource_id is None:
            return None, f"scope '{r['scope'] or ''}' must be AIS:<consentId> or PIS:<paymentId>"
        if kind == "PIS":
            payment = self.state.payments.get(resource_id)
            if payment is None or payment.tpp_id != r["client_id"]:
                return None, "the payment in the scope is unknown for this client"
            if payment.status not in ("ACTC", "ACCP"):
                return None, f"payment is '{payment.status}', expected ACTC"
            if payment.redirect_uri is not None and payment.redirect_uri != r["redirect_uri"]:
                return None, "redirect_uri differs from the Client-Redirect-URI sent with the payment"
            return payment, None

        consent = self.state.consents.get(resource_id)
        if consent is None or consent.tpp_id != r["client_id"]:
            return None, "the consent in the scope is unknown for this client"
        if consent.status != "received":
            return None, f"consent is '{consent.status}', expected 'received'"
        if consent.redirect_uri is not None and consent.redirect_uri != r["redirect_uri"]:
            return None, "redirect_uri differs from the Client-Redirect-URI sent with the consent"

        return consent, None

    def _approve(self, r: dict, consent: Consent | Payment) -> Response:
        code = MockState.new_token()
        with self.state.lock:
            self.state.codes[code] = AuthorizationCode(code, r["client_id"], r["redirect_uri"], self._normalize_scope(r["scope"]),
                                                       consent.id, r["code_challenge"], utc_now() + timedelta(minutes=2))
            for a in self.state.authorisations.values():
                if a.consent_id == consent.id:
                    a.sca_status = "psuAuthenticated"

        return redirect(self._append_query(r["redirect_uri"], [("code", code), ("state", r["state"])]))

    def token(self, request: Request) -> Response:
        cert_tpp_id = organization_id(request.client_cert.subject) if request.client_cert is not None else None
        if cert_tpp_id is None:
            return oauth_problem("invalid_client", "Mutual TLS with the TPP's QWAC is required (tls_client_auth)", 401)

        form = request.form()

        def f(name):
            return (form.get(name) or [None])[0]

        if f("client_id") != cert_tpp_id:
            return oauth_problem("invalid_client", f"client_id must be the QWAC's TPP id '{cert_tpp_id}'", 401)

        grant_type = f("grant_type") or ""
        with self.state.lock:
            if grant_type == "authorization_code":
                code = self.state.codes.get(f("code") or "")
                if code is None or code.used or code.expires_utc < utc_now():
                    return oauth_problem("invalid_grant", "Authorization code is unknown, expired or already used")
                if code.client_id != cert_tpp_id or code.redirect_uri != f("redirect_uri"):
                    return oauth_problem("invalid_grant", "client_id or redirect_uri does not match the authorization request")
                if self._pkce(f("code_verifier") or "") != code.code_challenge:
                    return oauth_problem("invalid_grant", "code_verifier does not match code_challenge (PKCE)")

                code.used = True
                consent = self.state.consents.get(code.consent_id)
                if consent is not None and consent.status == "received":
                    consent.status = "valid"
                payment = self.state.payments.get(code.consent_id)
                if payment is not None:
                    self._authorise_payment(payment)
                for a in self.state.authorisations.values():
                    if a.consent_id == code.consent_id:
                        a.sca_status = "finalised"

                return self._issue_tokens(cert_tpp_id, code.consent_id, code.scope)

            if grant_type == "refresh_token":
                refresh = self.state.refresh_tokens.pop(f("refresh_token") or "", None)
                if refresh is None or refresh.client_id != cert_tpp_id:
                    return oauth_problem("invalid_grant", "Refresh token is unknown or belongs to another client")
                consent = self.state.consents.get(refresh.consent_id)
                if refresh.consent_id not in self.state.payments and (consent is None or consent.status != "valid"):
                    return oauth_problem("invalid_grant", "The consent is no longer valid")

                return self._issue_tokens(cert_tpp_id, refresh.consent_id, refresh.scope)

        return oauth_problem("unsupported_grant_type", "Use authorization_code or refresh_token")

    def _issue_tokens(self, client_id: str, consent_id: str, scope: str) -> Response:
        lifetime = self.options.access_token_lifetime_seconds
        access = AccessToken(MockState.new_token(), client_id, consent_id, scope, utc_now() + timedelta(seconds=lifetime))
        refresh = RefreshToken(MockState.new_token(), client_id, consent_id, scope)
        self.state.access_tokens[access.token] = access
        self.state.refresh_tokens[refresh.token] = refresh

        return json_response({
            "access_token": access.token,
            "token_type": "Bearer",
            "expires_in": lifetime,
            "refresh_token": refresh.token,
            "scope": scope,
        })

    @staticmethod
    def _normalize_scope(scope: str | None) -> str:
        """The first scope, e.g. "AIS:<id>" from "AIS:<id> openid"."""
        parts = (scope or "").split()
        return parts[0] if parts else ""

    def _resource_from_scope(self, scope: str | None) -> tuple[str | None, str | None]:
        """("AIS", consent id) or ("PIS", payment id) from the scope; (None, None) if it is neither."""
        parts = self._normalize_scope(scope).split(":", 1)
        if len(parts) == 2 and parts[0].upper() in ("AIS", "PIS") and _is_guid(parts[1]):
            return parts[0].upper(), parts[1]
        return None, None

    @staticmethod
    def _pkce(verifier: str) -> str:
        return b64url_encode(hashlib.sha256(verifier.encode("ascii", errors="replace")).digest())

    def _redirect_with_error(self, r: dict, error: str, description: str) -> Response:
        if not _is_absolute(r["redirect_uri"]):
            return self._error_page(description)
        return redirect(self._append_query(r["redirect_uri"], [("error", error), ("error_description", description), ("state", r["state"])]))

    @staticmethod
    def _append_query(url: str, items) -> str:
        query = "&".join(f"{k}={_escape(v)}" for k, v in items if v is not None)
        return url + ("&" if "?" in url else "?") + query

    @staticmethod
    def _error_page(message: str) -> Response:
        return html_response('<!doctype html><meta charset=utf-8><title>MockBank error</title><body style="font-family:system-ui;margin:40px">'
                             f"<h2>MockBank: authorization error</h2><p>{html.escape(message)}</p></body>", 400)

    def _login_page(self, r: dict, consent: Consent | Payment) -> Response:
        e = html.escape
        if isinstance(consent, Payment):
            creditor = consent.body.get("creditorAccount", {}).get("iban", "")
            what = (f"<p><b>{e(r['client_id'] or '')}</b> asks you to pay <b>{consent.amount} {e(consent.currency)}</b> "
                    f"({e(consent.product)}) to <code>{e(creditor)}</code> {e(consent.body.get('creditorName') or '')}"
                    f" from <code>{e(consent.debtor_iban or 'an account you choose')}</code>.</p>")
        else:
            accounts = "".join(f"<li>{e(a.name)} — {e(a.iban)} ({e(a.currency)})</li>" for a in self.state.accounts)
            what = (f"<p><b>{e(r['client_id'] or '')}</b> asks for <code>{e(consent.consent_type)}</code> access "
                    f"({e(', '.join(consent.rights))}) until {consent.valid_to.isoformat()} to:</p><ul>{accounts}</ul>")
        hidden = "".join(f'<input type=hidden name="{name}" value="{e(r[name] or "")}">'
                         for name in ("response_type", "client_id", "redirect_uri", "scope", "state", "code_challenge", "code_challenge_method"))
        return html_response(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MockBank login</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; background: #f4f6f8; margin: 0; padding: 32px 16px; color: #1d1d1f; }}
  .card {{ max-width: 460px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }} .muted {{ color: #6e6e73; font-size: 13px; }}
  button {{ font: inherit; padding: 10px 18px; border-radius: 8px; border: 0; cursor: pointer; margin-right: 8px; }}
  .approve {{ background: #1a7f37; color: #fff; }} .deny {{ background: #e5e5ea; }}
  code {{ background: #f0f0f3; padding: 1px 4px; border-radius: 4px; }}
</style></head><body><div class="card">
<h1>MockBank — {'confirm payment' if isinstance(consent, Payment) else 'confirm access'}</h1>
<p class="muted">Sandbox PSU <b>testuser1</b> is logged in (no password needed in the mock).</p>
{what}
<form method="post" action="/oauth/authorize">{hidden}
  <button class="approve" name="decision" value="approve">Approve</button>
  <button class="deny" name="decision" value="deny">Deny</button>
</form>
</div></body></html>""")

    # ---- dashboard -------------------------------------------------------------------------------------------------

    def dashboard(self, request: Request) -> Response:
        e = html.escape
        o = self.options
        consents = sorted(self.state.consents.values(), key=lambda c: c.created_utc, reverse=True)[:50]
        rows = "".join(f"<tr><td>{c.created_utc:%H:%M:%S}</td><td><code>{e(c.id)}</code></td><td>{e(c.tpp_id)}</td>"
                       f"<td>{e(c.consent_type)}</td><td>{e(c.status)}</td></tr>" for c in consents)
        return html_response(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="5"><title>MockBank</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;margin:24px auto;max-width:960px;padding:0 16px}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #e5e5ea;padding:6px;text-align:left}}code{{font-size:12px}}</style></head><body>
<h1>ApiTestTool MockBank <small style="font-weight:normal;color:#6e6e73">(Python)</small></h1>
<p>XS2A: <code>{o.https_base_url}/v2</code> · Discovery: <code>{o.https_base_url}/.well-known/oauth-authorization-server</code> · Auto-approve: <b>{e(o.auto_approve)}</b></p>
<p>{len(self.state.consents)} consent(s), {len(self.state.access_tokens)} access token(s). Refreshes every 5 s.</p>
<table><thead><tr><th>Created (UTC)</th><th>Consent</th><th>TPP</th><th>Type</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>""")
