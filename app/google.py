"""Вход через Google (OpenID Connect, authorization code flow с PKCE)."""

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleAuthError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def pkce_pair() -> tuple[str, str]:
    """Секрет (verifier) остаётся у сервера, в Google уходит только его хеш (challenge)."""
    verifier = secrets.token_urlsafe(64)
    return verifier, _b64url(hashlib.sha256(verifier.encode()).digest())


def auth_url(client_id: str, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str, verifier: str, redirect_uri: str, client_id: str, client_secret: str) -> dict:
    data = urllib.parse.urlencode({
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
    }).encode()
    request = urllib.request.Request(TOKEN_URL, data=data, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError) as exc:
        raise GoogleAuthError(f"обмен кода не удался: {exc}") from None


def verify_id_token(id_token: str, client_id: str, nonce: str, now: float | None = None) -> dict:
    """Проверяет утверждения id_token.

    Подпись не проверяется намеренно: токен получен напрямую от token endpoint Google
    по HTTPS в обмен на client secret, а для такого случая документация Google
    (OpenID Connect, «Obtain user information from the ID token») разрешает обойтись
    без проверки подписи. Поэтому эту функцию нельзя применять к токенам,
    пришедшим из браузера.
    """
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise GoogleAuthError("id_token не разбирается") from None

    now = time.time() if now is None else now
    checks = {
        "aud": claims.get("aud") == client_id,
        "iss": claims.get("iss") in ISSUERS,
        "exp": isinstance(claims.get("exp"), (int, float)) and claims["exp"] > now,
        "nonce": isinstance(claims.get("nonce"), str) and secrets.compare_digest(claims["nonce"], nonce),
        "email_verified": claims.get("email_verified") is True,
        "sub": bool(claims.get("sub")),
        "email": bool(claims.get("email")),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise GoogleAuthError(f"id_token не прошёл проверку: {', '.join(failed)}")
    return claims
