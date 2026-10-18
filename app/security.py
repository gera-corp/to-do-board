import hashlib
import hmac
import secrets
import threading
import time

ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, expected = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


# Для несуществующего логина пароль сверяется с этим хешем: ответ занимает столько же
# времени, сколько для настоящего, и по секундомеру не узнать, какие логины есть.
DECOY_PASSWORD_HASH = hash_password(secrets.token_hex(16))


class LoginLimiter:
    """Считает неудачные попытки входа по ключу (логин, IP) в скользящем окне."""

    def __init__(self, max_failures: int, window_seconds: float = 900, clock=None):
        self.max_failures = max_failures
        self.window = window_seconds
        self.clock = clock or time.monotonic
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        cutoff = self.clock() - self.window
        recent = [t for t in self.failures.get(key, []) if t > cutoff]
        if recent:
            self.failures[key] = recent
        else:
            self.failures.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        with self.lock:
            return len(self._recent(key)) >= self.max_failures

    def fail(self, key: str) -> None:
        with self.lock:
            self._recent(key)
            self.failures.setdefault(key, []).append(self.clock())

    def reset(self, key: str) -> None:
        with self.lock:
            self.failures.pop(key, None)
