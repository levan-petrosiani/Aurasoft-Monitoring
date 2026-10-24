from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric import ec

from aqt import certificates
from aqt.certificates import Certificate, Psd2CertificateInfo
from aqt.signing import JwsSigner, SigningMode, compute_digest, verify_digest, verify_response

NOW = datetime.now(timezone.utc)
SEAL = certificates.create_tpp_certificate("Test QSealC", "PSDGE-NBG-TEST")
HEADERS = [("X-Request-ID", "6b1c5d43-6b8e-4c2e-9b8c-0d3f6c1b2a11"), ("PSU-IP-Address", "1.2.3.4"),
           ("Client-Redirect-URI", "http://localhost:5199/callback")]
BODY = b'{"access":{}}'


def verify(signature, headers=HEADERS, body=BODY):
    h = dict(headers)
    h["Digest"] = signature.digest
    h["X-Jws-Signature"] = signature.jws_signature
    return verify_response(h, body, 0, NOW)


def test_signed_request_verifies_and_reports_the_signer():
    signature = JwsSigner(SEAL).sign(HEADERS, BODY, NOW)
    assert signature.signed_headers == ["x-request-id", "client-redirect-uri", "digest"]
    assert signature.algorithm == "PS256"
    result = verify(signature)
    assert result.is_valid, result.errors
    assert result.signer_organization_id == "PSDGE-NBG-TEST"


def test_corrupt_signature_and_wrong_digest_are_detected():
    corrupt = verify(JwsSigner(SEAL).sign(HEADERS, BODY, NOW, SigningMode.CORRUPT_SIGNATURE))
    assert "Signature value is invalid" in corrupt.errors

    wrong = verify(JwsSigner(SEAL).sign(HEADERS, BODY, NOW, SigningMode.WRONG_DIGEST))
    assert "Digest does not match the body" in wrong.errors


def test_tampered_header_breaks_the_signature():
    signature = JwsSigner(SEAL).sign(HEADERS, BODY, NOW)
    tampered = [(k, "http://evil" if k == "Client-Redirect-URI" else v) for k, v in HEADERS]
    assert "Signature value is invalid" in verify(signature, tampered).errors


def test_missing_signature_and_clock_skew():
    assert not verify_response({}, b"", 200, NOW).is_present
    signature = JwsSigner(SEAL).sign(HEADERS, BODY, NOW + timedelta(minutes=10))
    assert any("future" in w for w in verify(signature).warnings)


def test_ecdsa_keys_sign_es256():
    key = ec.generate_private_key(ec.SECP256R1())
    rsa_cert = certificates.create_tpp_certificate("x", "PSDGE-NBG-EC")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    cert = (x509.CertificateBuilder().subject_name(rsa_cert.cert.subject).issuer_name(rsa_cert.cert.subject)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(NOW - timedelta(days=1)).not_valid_after(NOW + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    signature = JwsSigner(Certificate(cert, key)).sign(HEADERS, BODY, NOW)
    assert signature.algorithm == "ES256"
    assert verify(signature).is_valid


def test_digest():
    assert compute_digest(b"") == "SHA-256=47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="
    assert verify_digest(compute_digest(b"abc"), b"abc") is None
    assert verify_digest("MD5=YWJj", b"abc").startswith("Unsupported")
    assert verify_digest("SHA-256=abc", b"abc") == "Digest value is not valid Base64"


def test_certificate_info_matches_dotnet_format():
    info = Psd2CertificateInfo.of(SEAL)
    assert info.subject == "CN=Test QSealC, O=ApiTestTool, OID.2.5.4.97=PSDGE-NBG-TEST, C=GE"
    assert info.organization_id == "PSDGE-NBG-TEST"
    assert info.key_algorithm == "RSA" and info.has_private_key
    assert 360 <= info.days_to_expiry(NOW) <= 365
