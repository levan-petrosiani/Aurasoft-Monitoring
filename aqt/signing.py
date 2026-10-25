"""
Request signing and response verification for the Georgian XS2A / Open Banking Europe JWS profile:
a detached JWS (JAdES, b64=false) over selected HTTP headers in X-Jws-Signature, plus the body Digest (RFC 3230).
"""

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding

from .certificates import Certificate, Psd2CertificateInfo
from .errors import ConfigurationError

JWS_SIGNATURE_HEADER = "X-Jws-Signature"
DIGEST_HEADER = "Digest"
SIGD_MECHANISM = "http://uri.etsi.org/19182/HttpHeaders"

_VALID_ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"}


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    s = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


# ---- Digest ---------------------------------------------------------------------------------------------------

def compute_digest(body: bytes) -> str:
    """SHA-256=Base64(SHA-256(body)); an absent body is hashed as an empty byte array."""
    return "SHA-256=" + base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")


def verify_digest(header_value: str, body: bytes) -> str | None:
    """Checks a received Digest header against a body. Returns an error message, or None when it matches."""
    parts = header_value.split("=", 1)
    if len(parts) != 2:
        return f"Invalid Digest header '{header_value}'"

    try:
        expected = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        return "Digest value is not valid Base64"

    algorithm = {"SHA-256": hashlib.sha256, "SHA-384": hashlib.sha384, "SHA-512": hashlib.sha512}.get(parts[0].upper())
    if algorithm is None:
        return f"Unsupported digest algorithm '{parts[0]}'"

    if not hmac.compare_digest(algorithm(body).digest(), expected):
        return "Digest does not match the body"
    return None


# ---- Signing --------------------------------------------------------------------------------------------------

class SigningMode(Enum):
    """How a request is signed. Anything other than NORMAL deliberately breaks the signature for negative tests."""
    NORMAL = "normal"
    NONE = "none"
    CORRUPT_SIGNATURE = "corruptSignature"
    WRONG_DIGEST = "wrongDigest"


@dataclass(frozen=True)
class SignatureResult:
    jws_signature: str
    digest: str
    signed_headers: list[str]
    algorithm: str


def is_signing_header(lower_case_name: str) -> bool:
    """Headers that go into sigD.pars (x-request-id plus PSU/client headers); digest is always appended last."""
    return lower_case_name in ("x-request-id", "psu-id", "psu-corporate-id", "client-redirect-uri", "api-contract-id")


class JwsSigner:
    """Creates the X-Jws-Signature and Digest request headers."""

    def __init__(self, qsealc: Certificate):
        if not qsealc.has_private_key:
            raise ConfigurationError("QSealC certificate has no private key")
        self._certificate = qsealc
        self._x5c = [base64.b64encode(c.public_bytes(Encoding.DER)).decode("ascii") for c in [qsealc.cert] + qsealc.chain]
        key = qsealc.key
        if isinstance(key, rsa.RSAPrivateKey):
            self._alg = "PS256"
        elif isinstance(key, ec.EllipticCurvePrivateKey):
            self._alg = "ES256"
        else:
            raise ConfigurationError("QSealC must have an RSA or ECDSA key")

    def sign(self, headers: list[tuple[str, str]], body: bytes, now_utc: datetime,
             mode: SigningMode = SigningMode.NORMAL) -> SignatureResult:
        signing_headers = [(name.lower(), value) for name, value in headers if is_signing_header(name.lower())]
        pars = [k for k, _ in signing_headers] + ["digest"]

        # Key order matches the original encoder so signatures are comparable when debugging.
        protected_header = {
            "typ": "JOSE",
            "b64": False,
            "x5c": self._x5c,
            "crit": ["b64", "sigT", "sigD"],
            "sigT": now_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sigD": {"mId": SIGD_MECHANISM, "pars": pars},
            "alg": self._alg,
        }
        protected_b64 = b64url_encode(json.dumps(protected_header, separators=(",", ":")).encode("utf-8"))

        digest = compute_digest(body)
        if mode == SigningMode.WRONG_DIGEST:
            digest = compute_digest(b"this is not the request body")
        signing_headers.append(("digest", digest))

        headers_to_sign = "\n".join(f"{k}: {v}" for k, v in signing_headers)
        signature = bytearray(self._sign_data(f"{protected_b64}.{headers_to_sign}".encode("utf-8")))

        if mode == SigningMode.CORRUPT_SIGNATURE:
            signature[-1] ^= 0xFF

        return SignatureResult(f"{protected_b64}..{b64url_encode(bytes(signature))}", digest, pars, self._alg)

    def _sign_data(self, data: bytes) -> bytes:
        key = self._certificate.key
        if isinstance(key, rsa.RSAPrivateKey):
            return key.sign(data, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())

        # JWS needs the IEEE P1363 form (r || s), not DER.
        der = key.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        size = (key.curve.key_size + 7) // 8
        return r.to_bytes(size, "big") + s.to_bytes(size, "big")


# ---- Verification ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class JwsVerificationResult:
    is_present: bool
    errors: list[str]
    warnings: list[str]
    signer_subject: str | None
    signer_organization_id: str | None

    @property
    def is_valid(self) -> bool:
        return self.is_present and not self.errors


def verify_response(headers: dict[str, str], body: bytes, status_code: int, now_utc: datetime) -> JwsVerificationResult:
    """
    Verifies an X-Jws-Signature (on a response, or on a request when status_code is 0): protected header rules,
    signature value over the headers listed in sigD.pars, and the body Digest. The certificate chain is not
    checked, because sandbox certificates are often issued by test CAs; the signer is reported instead.
    """
    errors: list[str] = []
    warnings: list[str] = []
    h = {k.lower(): v for k, v in headers.items()}

    jws = h.get(JWS_SIGNATURE_HEADER.lower())
    if jws is None or not jws.strip():
        return JwsVerificationResult(False, ["X-Jws-Signature header is missing"], warnings, None, None)

    def fail(error):
        return JwsVerificationResult(True, [error], warnings, None, None)

    parts = jws.split("..")
    if len(parts) != 2:
        return fail("X-Jws-Signature is not a detached JWS ('<header>..<signature>')")

    try:
        header = json.loads(b64url_decode(parts[0]))
        if not isinstance(header, dict):
            raise ValueError("not a JSON object")
    except (ValueError, binascii.Error, UnicodeDecodeError) as ex:
        return fail(f"Protected header cannot be decoded: {ex}")

    # b64 must be false
    if header.get("b64") is not False:
        errors.append("Protected header: 'b64' must be false")

    # alg
    alg = header.get("alg") if isinstance(header.get("alg"), str) else None
    if alg is None or alg not in _VALID_ALGORITHMS:
        errors.append(f"Protected header: invalid 'alg' ({alg or 'missing'})")

    # crit
    crit = _string_array(header, "crit")
    for required in ("b64", "sigT", "sigD"):
        if required not in crit:
            errors.append(f"Protected header: 'crit' must contain '{required}'")

    # typ
    if "typ" in header and header["typ"] != "JOSE":
        warnings.append(f"Protected header: 'typ' should be 'JOSE' (got '{header['typ']}')")

    # sigT: clock skew between our machine and the bank is possible, so these are warnings.
    sig_t = _parse_time(header.get("sigT"))
    if sig_t is not None:
        diff = (sig_t - now_utc).total_seconds()
        if diff > 5:
            warnings.append(f"sigT is {diff:.0f}s in the future (clock skew?)")
        elif diff < -120:
            warnings.append(f"sigT is {-diff:.0f}s in the past")
    else:
        errors.append("Protected header: 'sigT' is missing or not a valid UTC time")

    # sigD
    pars: list[str] = []
    sig_d = header.get("sigD")
    if isinstance(sig_d, dict):
        if sig_d.get("mId") != SIGD_MECHANISM:
            errors.append(f"Protected header: 'sigD.mId' must be '{SIGD_MECHANISM}'")
        pars = _string_array(sig_d, "pars")
    else:
        errors.append("Protected header: 'sigD' is missing")

    for required in ("x-request-id", "digest"):
        if required not in pars:
            errors.append(f"Protected header: 'sigD.pars' must contain '{required}'")

    # x5c
    x5c = _string_array(header, "x5c")
    signer = None
    if not x5c:
        errors.append("Protected header: 'x5c' (signing certificate) is missing")
    else:
        try:
            signer = x509.load_der_x509_certificate(base64.b64decode(x5c[0], validate=True))
        except (ValueError, binascii.Error) as ex:
            errors.append(f"Protected header: 'x5c[0]' is not a valid certificate: {ex}")

    # Signature value
    if signer is not None and alg in _VALID_ALGORITHMS and pars:
        lines = []
        missing = False
        for key in pars:
            if key == "(response-status)":
                value = str(status_code)
            elif key == "content-type":
                value = h.get(key)
                value = value.lower() if value is not None else None
            else:
                value = h.get(key)

            if value is None:
                errors.append(f"Header '{key}' listed in sigD.pars is not present in the response")
                missing = True
                continue
            lines.append(f"{key}: {value}")

        if not missing:
            data = f"{parts[0]}.{chr(10).join(lines)}".encode("utf-8")
            try:
                signature = b64url_decode(parts[1])
                if not _verify_signature(signer, alg, data, signature):
                    errors.append("Signature value is invalid")
            except (ValueError, binascii.Error):
                errors.append("Signature value is not valid Base64url")

    # Digest
    digest = h.get("digest")
    if digest is not None:
        error = verify_digest(digest, body)
        if error:
            errors.append(error)
    else:
        errors.append("Digest header is missing")

    info = Psd2CertificateInfo.from_cert(signer) if signer is not None else None
    return JwsVerificationResult(True, errors, warnings, info.subject if info else None, info.organization_id if info else None)


def _verify_signature(cert: x509.Certificate, alg: str, data: bytes, signature: bytes) -> bool:
    hash_algorithm = {"256": hashes.SHA256(), "384": hashes.SHA384()}.get(alg[2:], hashes.SHA512())
    key = cert.public_key()
    try:
        if alg.startswith("ES"):
            if not isinstance(key, ec.EllipticCurvePublicKey) or len(signature) % 2:
                return False
            half = len(signature) // 2
            der = encode_dss_signature(int.from_bytes(signature[:half], "big"), int.from_bytes(signature[half:], "big"))
            key.verify(der, data, ec.ECDSA(hash_algorithm))
            return True

        if not isinstance(key, rsa.RSAPublicKey):
            return False
        pad = (padding.PSS(mgf=padding.MGF1(hash_algorithm), salt_length=padding.PSS.DIGEST_LENGTH)
               if alg.startswith("PS") else padding.PKCS1v15())
        key.verify(signature, data, pad, hash_algorithm)
        return True
    except InvalidSignature:
        return False


def _string_array(obj: dict, name: str) -> list[str]:
    value = obj.get(name)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
