import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from django.conf import settings


class InvalidToken(ValueError):
    pass


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(token: str) -> bytes:
    padding = "=" * ((4 - len(token) % 4) % 4)
    return base64.urlsafe_b64decode(token + padding)


def _derive_key() -> bytes:
    seed = f"{settings.SECRET_KEY}|swapmall-url-params-v1".encode("utf-8")
    return hashlib.sha256(seed).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_value(value: str) -> str:
    plaintext = value.encode("utf-8")
    key = _derive_key()
    nonce = secrets.token_bytes(16)
    stream = _keystream(key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    tag = hmac.new(key, b"tag" + nonce + ciphertext, hashlib.sha256).digest()[:16]
    return _b64encode(nonce + ciphertext + tag)


def decrypt_value(token: str) -> str:
    try:
        raw = _b64decode(token)
    except Exception as exc:
        raise InvalidToken("Token is not valid base64") from exc

    if len(raw) < 33:
        raise InvalidToken("Token is too short")

    nonce = raw[:16]
    tag = raw[-16:]
    ciphertext = raw[16:-16]
    key = _derive_key()

    expected_tag = hmac.new(key, b"tag" + nonce + ciphertext, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected_tag):
        raise InvalidToken("Token signature mismatch")

    stream = _keystream(key, nonce, len(ciphertext))
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream))
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidToken("Token payload decode failed") from exc


def encrypt_int(value: int) -> str:
    return encrypt_value(str(int(value)))


def decrypt_int(token: str) -> int:
    raw = decrypt_value(token)
    if not raw.isdigit():
        raise InvalidToken("Token is not an integer")
    return int(raw)


def encrypt_payload(payload: dict[str, Any]) -> str:
    return encrypt_value(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def decrypt_payload(token: str) -> dict[str, Any]:
    raw = decrypt_value(token)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidToken("Token payload is not JSON") from exc
    if not isinstance(data, dict):
        raise InvalidToken("Token payload must be a JSON object")
    return data
