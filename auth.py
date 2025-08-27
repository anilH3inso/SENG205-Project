import os
import base64
import hmac
import hashlib

_ITER = 100_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return base64.b64encode(salt + key).decode("ascii")


def verify_password(password: str, secret: str) -> bool:
    raw = base64.b64decode(secret.encode("ascii"))
    salt, stored = raw[:16], raw[16:]
    new = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return hmac.compare_digest(new, stored)
