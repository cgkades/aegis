"""ChatGPT account OAuth (device-code style) for subscription-backed access.

This implements a best-effort device authorization flow against OpenAI's
auth.openai.com endpoints (same family of flow used by Codex "Sign in with
ChatGPT"). Endpoints can change; the settings UI also supports pasting a
bearer access token manually.

Tokens are stored under ~/.config/aegis/credentials/ (chmod 600).
"""

from __future__ import annotations

import json
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aegis.util.logging import get_logger

log = get_logger("llm.chatgpt_oauth")

# Public client id used by OpenAI desktop/CLI ChatGPT login (Codex family).
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE = "https://auth.openai.com"


@dataclass
class OAuthTokens:
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    expires_at: float = 0.0  # unix epoch
    account_id: str = ""
    email: str = ""
    obtained_at: float = field(default_factory=time.time)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        if not self.expires_at:
            return False
        return time.time() >= (self.expires_at - 60)

    @property
    def signed_in(self) -> bool:
        return bool(self.access_token) and not self.expired


def token_path(path: str | Path) -> Path:
    return Path(path).expanduser()


def load_tokens(path: str | Path) -> OAuthTokens | None:
    p = token_path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return OAuthTokens(
        access_token=str(data.get("access_token") or ""),
        refresh_token=str(data.get("refresh_token") or ""),
        id_token=str(data.get("id_token") or ""),
        expires_at=float(data.get("expires_at") or 0),
        account_id=str(data.get("account_id") or ""),
        email=str(data.get("email") or ""),
        obtained_at=float(data.get("obtained_at") or 0),
        raw=data.get("raw") or {},
    )


def save_tokens(path: str | Path, tokens: OAuthTokens) -> Path:
    p = token_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(tokens)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


def clear_tokens(path: str | Path) -> None:
    p = token_path(path)
    if p.is_file():
        p.unlink()


def status_dict(path: str | Path) -> dict[str, Any]:
    tok = load_tokens(path)
    if not tok or not tok.access_token:
        return {"signed_in": False, "email": "", "expires_at": 0, "expired": True}
    return {
        "signed_in": tok.signed_in,
        "email": tok.email or tok.account_id or "(signed in)",
        "expires_at": tok.expires_at,
        "expired": tok.expired,
        "has_refresh": bool(tok.refresh_token),
    }


def _http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = Request(url, data=data, headers=hdrs, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OAuth HTTP {exc.code}: {err_body[:400]}") from exc
    except URLError as exc:
        raise RuntimeError(f"OAuth network error: {exc.reason}") from exc


@dataclass
class DeviceAuthSession:
    user_code: str
    device_auth_id: str
    verification_url: str
    interval_s: float = 5.0
    expires_in_s: float = 900.0


def start_device_auth(
    *,
    auth_base: str = AUTH_BASE,
    client_id: str = DEFAULT_CLIENT_ID,
) -> DeviceAuthSession:
    """Request a user code for ChatGPT device login."""
    url = f"{auth_base.rstrip('/')}/api/accounts/deviceauth/usercode"
    # Payload shape has varied; try common fields
    data = _http_json(
        "POST",
        url,
        body={"client_id": client_id},
    )
    user_code = str(
        data.get("user_code") or data.get("userCode") or data.get("code") or ""
    )
    device_id = str(
        data.get("device_auth_id")
        or data.get("deviceAuthId")
        or data.get("device_code")
        or data.get("id")
        or ""
    )
    verify = str(
        data.get("verification_uri")
        or data.get("verification_url")
        or data.get("verificationUri")
        or f"{auth_base.rstrip('/')}/codex/device"
    )
    if not user_code:
        # Some deployments return nested data
        nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        user_code = str(nested.get("user_code") or nested.get("userCode") or "")
        device_id = device_id or str(
            nested.get("device_auth_id") or nested.get("deviceAuthId") or ""
        )
    if not user_code:
        raise RuntimeError(
            "Device auth did not return a user_code. "
            f"Response keys: {list(data.keys())}. "
            "You can paste an access token manually in Settings."
        )
    interval = float(data.get("interval") or data.get("interval_s") or 5)
    expires = float(data.get("expires_in") or data.get("expires_in_s") or 900)
    return DeviceAuthSession(
        user_code=user_code,
        device_auth_id=device_id or user_code,
        verification_url=verify,
        interval_s=interval,
        expires_in_s=expires,
    )


def poll_device_auth(
    session: DeviceAuthSession,
    *,
    auth_base: str = AUTH_BASE,
    client_id: str = DEFAULT_CLIENT_ID,
    timeout_s: float | None = None,
) -> OAuthTokens:
    """Poll until the user completes browser approval or timeout."""
    deadline = time.time() + (timeout_s or session.expires_in_s)
    url = f"{auth_base.rstrip('/')}/api/accounts/deviceauth/token"
    while time.time() < deadline:
        try:
            data = _http_json(
                "POST",
                url,
                body={
                    "client_id": client_id,
                    "device_auth_id": session.device_auth_id,
                    "user_code": session.user_code,
                },
            )
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "pending" in msg or "authorization_pending" in msg or " 400" in msg:
                time.sleep(session.interval_s)
                continue
            if "slow" in msg:
                time.sleep(session.interval_s + 2)
                continue
            raise

        access = str(
            data.get("access_token")
            or (data.get("tokens") or {}).get("access_token")
            or ""
        )
        if access:
            expires_in = float(
                data.get("expires_in")
                or (data.get("tokens") or {}).get("expires_in")
                or 3600
            )
            refresh = str(
                data.get("refresh_token")
                or (data.get("tokens") or {}).get("refresh_token")
                or ""
            )
            email = str(
                data.get("email")
                or (data.get("user") or {}).get("email")
                or data.get("account_id")
                or ""
            )
            return OAuthTokens(
                access_token=access,
                refresh_token=refresh,
                id_token=str(data.get("id_token") or ""),
                expires_at=time.time() + expires_in,
                email=email,
                account_id=str(data.get("account_id") or ""),
                raw=data,
            )
        # still pending
        time.sleep(session.interval_s)
    raise TimeoutError("ChatGPT device login timed out")


def login_with_device_code(
    token_file: str | Path,
    *,
    auth_base: str = AUTH_BASE,
    client_id: str = DEFAULT_CLIENT_ID,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Start device login: open browser, poll, save tokens."""
    session = start_device_auth(auth_base=auth_base, client_id=client_id)
    verify = session.verification_url
    if session.user_code and "code=" not in verify:
        sep = "&" if "?" in verify else "?"
        verify = f"{verify}{sep}user_code={session.user_code}"
    if open_browser:
        webbrowser.open(verify)
    tokens = poll_device_auth(session, auth_base=auth_base, client_id=client_id)
    path = save_tokens(token_file, tokens)
    return {
        "ok": True,
        "user_code": session.user_code,
        "verification_url": verify,
        "token_path": str(path),
        "email": tokens.email,
        "status": status_dict(token_file),
    }


def save_manual_token(
    token_file: str | Path,
    access_token: str,
    *,
    refresh_token: str = "",
    email: str = "",
    expires_in_s: float = 3600,
) -> dict[str, Any]:
    tokens = OAuthTokens(
        access_token=access_token.strip(),
        refresh_token=refresh_token.strip(),
        email=email,
        expires_at=time.time() + expires_in_s,
    )
    path = save_tokens(token_file, tokens)
    return {"ok": True, "token_path": str(path), "status": status_dict(token_file)}
