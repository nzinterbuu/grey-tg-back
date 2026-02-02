"""Fernet encryption for Telethon session strings. Session strings are secret; treat like passwords."""

from cryptography.fernet import Fernet, InvalidToken

from config import SESSION_ENC_KEY


def _fernet() -> Fernet:
    raw = (SESSION_ENC_KEY or "").strip()
    if not raw:
        raise ValueError(
            "SESSION_ENC_KEY is not set. Activate the api venv, run "
            '"pip install -r requirements.txt", then generate a key with:\n'
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    key = raw.encode("utf-8") if isinstance(raw, str) else raw
    return Fernet(key)


def encrypt_session(session_string: str) -> str:
    """Encrypt a Telethon session string for storage."""
    return _fernet().encrypt(session_string.encode("utf-8")).decode("ascii")


def decrypt_session(encrypted: str) -> str:
    """Decrypt a stored session string."""
    try:
        return _fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError(
            "Failed to decrypt session string; SESSION_ENC_KEY may have changed."
        ) from e
