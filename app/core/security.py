import base64
import hashlib
import os

from cryptography.fernet import Fernet

from app.core.config import settings


def _get_fernet() -> Fernet:
    key = settings.db_encryption_key
    if not key:
        # Generate a throwaway key in dev — not for production
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    raw = base64.urlsafe_b64decode(key.encode() + b"==")
    return Fernet(base64.urlsafe_b64encode(raw[:32]))


def encrypt_bytes(data: bytes) -> bytes:
    return _get_fernet().encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    return _get_fernet().decrypt(data)


def encrypt_value(plaintext: str) -> str:
    return encrypt_bytes(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    return decrypt_bytes(ciphertext.encode()).decode()


def hash_field(value: str) -> str:
    """SHA-256 of a normalized string (strip + lowercase). Used for candidate dedup."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()
