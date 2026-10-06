import hashlib
import hmac
import secrets


SESSION_SECONDS = 30 * 24 * 60 * 60


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password between 12 and 128 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=131072, r=8, p=1,
        maxmem=256 * 1024 * 1024, dklen=64,
    )
    return f"scrypt-v1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        version, salt_hex, digest_hex = encoded.split("$")
        if version != "scrypt-v1" or len(salt_hex) != 32 or len(digest_hex) != 128:
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=131072, r=8, p=1,
            maxmem=256 * 1024 * 1024, dklen=64,
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()