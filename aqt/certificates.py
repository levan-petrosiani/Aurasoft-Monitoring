"""
eIDAS certificates (QWAC / QSealC): loading from PFX, throw-away self-signed certificates for local testing,
and the PSD2-relevant facts about a certificate.
"""

import datetime as dt
import ipaddress
import math
import os
import tempfile
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, ObjectIdentifier

from .errors import ConfigurationError
from .profiles import CertificateSettings

# OID of the organizationIdentifier attribute that carries the PSD2 authorisation number (TppId).
ORGANIZATION_IDENTIFIER_OID = "2.5.4.97"
_ORG_ID = ObjectIdentifier(ORGANIZATION_IDENTIFIER_OID)

# Attribute names as .NET prints them in X500DistinguishedName.Name.
_SHORT_NAMES = {
    "2.5.4.3": "CN", "2.5.4.10": "O", "2.5.4.11": "OU", "2.5.4.6": "C", "2.5.4.7": "L", "2.5.4.8": "S",
    "2.5.4.9": "STREET", "2.5.4.5": "SERIALNUMBER", "2.5.4.4": "SN", "2.5.4.42": "G", "2.5.4.12": "T",
    "1.2.840.113549.1.9.1": "E", "0.9.2342.19200300.100.1.25": "DC",
}


class Certificate:
    """A certificate with (optionally) its private key and the rest of its chain."""

    def __init__(self, cert: x509.Certificate, key=None, chain: list[x509.Certificate] | None = None):
        self.cert = cert
        self.key = key
        self.chain = chain or []
        self._files: tuple[str, str] | None = None

    @property
    def has_private_key(self) -> bool:
        return self.key is not None

    def der(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.DER)

    def pem_files(self) -> tuple[str, str]:
        """PEM files (certificate chain, unencrypted key) for Python's ssl module, created once and removed by close()."""
        if self._files is None:
            cert_pem = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in [self.cert] + self.chain)
            key_pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption())
            self._files = (_temp_file(cert_pem, ".crt"), _temp_file(key_pem, ".key"))
        return self._files

    def close(self):
        if self._files:
            for f in self._files:
                try:
                    os.remove(f)
                except OSError:
                    pass
            self._files = None


def _temp_file(data: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(prefix="aqt-", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


@dataclass(frozen=True)
class Psd2CertificateInfo:
    subject: str
    issuer: str
    thumbprint: str
    organization_id: str | None
    not_before_utc: dt.datetime
    not_after_utc: dt.datetime
    has_private_key: bool
    key_algorithm: str

    def days_to_expiry(self, now: dt.datetime) -> int:
        return math.floor((self.not_after_utc - now).total_seconds() / 86400)

    def is_valid_at(self, now: dt.datetime) -> bool:
        return self.not_before_utc <= now <= self.not_after_utc

    @staticmethod
    def from_cert(cert: x509.Certificate, has_private_key: bool = False) -> "Psd2CertificateInfo":
        public_key = cert.public_key()
        if isinstance(public_key, rsa.RSAPublicKey):
            algorithm = "RSA"
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            algorithm = "ECDSA"
        else:
            algorithm = cert.public_key_algorithm_oid.dotted_string

        return Psd2CertificateInfo(
            subject=format_name(cert.subject),
            issuer=format_name(cert.issuer),
            thumbprint=cert.fingerprint(hashes.SHA1()).hex().upper(),
            organization_id=organization_id(cert.subject),
            not_before_utc=cert.not_valid_before_utc,
            not_after_utc=cert.not_valid_after_utc,
            has_private_key=has_private_key,
            key_algorithm=algorithm)

    @staticmethod
    def of(certificate: Certificate) -> "Psd2CertificateInfo":
        return Psd2CertificateInfo.from_cert(certificate.cert, certificate.has_private_key)


def organization_id(name: x509.Name) -> str | None:
    for rdn in name.rdns:
        attributes = list(rdn)
        if len(attributes) == 1 and attributes[0].oid == _ORG_ID:
            value = attributes[0].value
            return value.decode() if isinstance(value, bytes) else value
    return None


def format_name(name: x509.Name) -> str:
    """Distinguished name in the .NET form, most specific first: "CN=..., O=..., OID.2.5.4.97=..., C=GE"."""
    parts = []
    for rdn in reversed(name.rdns):
        items = []
        for attribute in rdn:
            oid = attribute.oid.dotted_string
            label = _SHORT_NAMES.get(oid, f"OID.{oid}")
            value = attribute.value.decode() if isinstance(attribute.value, bytes) else str(attribute.value)
            if any(c in value for c in ',+="\n<>#;') or value != value.strip():
                value = '"' + value.replace('"', '""') + '"'
            items.append(f"{label}={value}")
        parts.append(" + ".join(items))
    return ", ".join(parts)


def load(settings: CertificateSettings, base_directory: str, name: str) -> Certificate:
    """Loads a certificate with its private key from a PFX file, or creates a self-signed one."""
    if settings.self_signed is not None:
        return create_tpp_certificate(settings.self_signed.common_name, settings.self_signed.organization_id)

    if settings.file and settings.file.strip():
        return _load_from_file(settings, base_directory, name)

    if settings.thumbprint and settings.thumbprint.strip():
        raise ConfigurationError(
            f"{name}: loading from the Windows certificate store ({settings.store or 'CurrentUser/My'}) is not "
            "supported by the Python edition; export the certificate with its private key to a PFX file and use 'file'")

    raise ConfigurationError(f"{name}: one of 'file', 'thumbprint' or 'selfSigned' must be specified")


def _load_from_file(settings: CertificateSettings, base_directory: str, name: str) -> Certificate:
    path = settings.file if os.path.isabs(settings.file) else os.path.abspath(os.path.join(base_directory, settings.file))
    if not os.path.isfile(path):
        raise ConfigurationError(f"{name}: certificate file not found: {path}")

    password = settings.password
    if settings.password_env and settings.password_env.strip():
        password = os.environ.get(settings.password_env)
        if password is None:
            raise ConfigurationError(f"{name}: environment variable '{settings.password_env}' with the certificate password is not set")

    with open(path, "rb") as f:
        data = f.read()

    candidates = [password.encode()] if password else [None, b""]
    last_error = None
    for candidate in candidates:
        try:
            key, cert, additional = pkcs12.load_key_and_certificates(data, candidate)
            break
        except (ValueError, TypeError) as ex:
            last_error = ex
    else:
        raise ConfigurationError(f"{name}: cannot open '{path}': {last_error}")

    if cert is None:
        raise ConfigurationError(f"{name}: '{path}' contains no certificate")
    if key is None:
        raise ConfigurationError(f"{name}: certificate '{path}' has no private key")

    return Certificate(cert, key, list(additional or []))


def create_tpp_certificate(common_name: str, org_id: str) -> Certificate:
    """Certificate that looks like a PSD2 QWAC/QSealC: RSA 2048 with an organizationIdentifier. Never for real banks."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = _name(common_name, "ApiTestTool", org_id)
    builder = (_builder(name, key)
               .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=True,
                                            data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False))
    return Certificate(builder.sign(key, hashes.SHA256()), key)


def create_localhost_server_certificate(org_id: str) -> Certificate:
    """TLS server certificate for localhost / 127.0.0.1 / ::1."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = _name("localhost", "ApiTestTool MockBank", org_id)
    builder = (_builder(name, key)
               .add_extension(x509.SubjectAlternativeName([
                   x509.DNSName("localhost"),
                   x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                   x509.IPAddress(ipaddress.ip_address("::1"))]), critical=False)
               .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
                                            data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False))
    return Certificate(builder.sign(key, hashes.SHA256()), key)


def _name(common_name: str, organization: str, org_id: str) -> x509.Name:
    # Encoded C first, like .NET does for "CN=..., O=..., OID.2.5.4.97=..., C=GE".
    return x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "GE"),
        x509.NameAttribute(_ORG_ID, org_id),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])


def _builder(name: x509.Name, key) -> x509.CertificateBuilder:
    now = dt.datetime.now(dt.timezone.utc)
    return (x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365)))
