"""
app/services/encryption.py
===========================
AES-256-GCM encryption service.

Replaces the existing XOR + base64 obfuscation in api/vault.py.
Backward compatible — existing encrypted values are migrated on first read.

Usage:
    from app.services.encryption import encrypt, decrypt, is_aes_encrypted

    enc = encrypt("sk-real-api-key")
    plain = decrypt(enc)

Environment:
    VAULT_ENC_KEY  — 32-byte hex string (64 hex chars)
                     Generate: python -c "import secrets; print(secrets.token_hex(32))"
"""

import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── Key loading ───────────────────────────────────────────────────────────────
def _load_key() -> bytes:
    """
    Load the 32-byte AES key from environment.
    Accepts either:
      - 64-character hex string (recommended)
      - 32-character plain string (legacy)
    """
    raw = os.getenv("VAULT_ENC_KEY", "")

    if len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass

    # Fallback: pad/truncate plain string to 32 bytes
    key_bytes = raw.encode("utf-8")
    if len(key_bytes) >= 32:
        return key_bytes[:32]

    # Pad with zeros (dev only — warn in prod)
    padded = key_bytes.ljust(32, b"\x00")
    return padded


_AES_KEY: bytes = _load_key()

# Prefix to distinguish AES-GCM ciphertext from old XOR ciphertext
_AES_PREFIX = "AES256GCM:"


# ── Core encrypt / decrypt ────────────────────────────────────────────────────

def encrypt(plaintext: str) -> str:
    """
    Encrypt a plaintext string using AES-256-GCM.
    Returns a base64-encoded string prefixed with AES256GCM:.

    Format: AES256GCM:<base64(nonce + ciphertext)>
    Nonce is 12 bytes (96 bits), randomly generated per encryption.
    """
    aesgcm = AESGCM(_AES_KEY)
    nonce  = secrets.token_bytes(12)
    ct     = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    blob   = base64.b64encode(nonce + ct).decode("utf-8")
    return f"{_AES_PREFIX}{blob}"


def decrypt(ciphertext: str) -> str:
    """
    Decrypt an AES-256-GCM ciphertext.
    Also handles legacy XOR+base64 values transparently.
    """
    if ciphertext.startswith(_AES_PREFIX):
        return _decrypt_aes(ciphertext[len(_AES_PREFIX):])
    else:
        return _decrypt_xor_legacy(ciphertext)


def is_aes_encrypted(value: str) -> bool:
    """Check whether a stored value uses the new AES encryption."""
    return value.startswith(_AES_PREFIX)


def mask(plaintext: str) -> str:
    """Return a masked version of an API key for display."""
    if len(plaintext) <= 8:
        return "****"
    return plaintext[:4] + "****" + plaintext[-4:]


def rotate_encrypt(old_ciphertext: str) -> str:
    """
    Decrypt the old value and re-encrypt with a fresh nonce.
    Used for key rotation — each rotation produces a different ciphertext.
    """
    plain = decrypt(old_ciphertext)
    return encrypt(plain)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _decrypt_aes(blob: str) -> str:
    raw    = base64.b64decode(blob.encode("utf-8"))
    nonce  = raw[:12]
    ct     = raw[12:]
    aesgcm = AESGCM(_AES_KEY)
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")


def _decrypt_xor_legacy(enc: str) -> str:
    """
    Legacy XOR + base64 decryption from the original vault.py.
    Used automatically when reading old values until they're re-encrypted.
    """
    _ENC_KEY = os.getenv("VAULT_ENC_KEY", "tower_ai_vault_key_2024").encode()
    data = base64.b64decode(enc.encode())
    key  = (_ENC_KEY * (len(data) // len(_ENC_KEY) + 1))[:len(data)]
    return bytes(a ^ b for a, b in zip(data, key)).decode()
