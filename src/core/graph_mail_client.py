"""
Stdlib Microsoft Graph mail client for Bloomberg news ingestion.

Supports app-only client credentials and delegated device-code auth. Delegated
auth reads the signed-in user's mailbox and caches refresh tokens locally.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from config.settings import get_api_key

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_DELEGATED_SCOPE = "offline_access User.Read Mail.Read"
TOKEN_EXPIRY_SAFETY_SECONDS = 120
DEFAULT_TOKEN_CACHE_PATH = Path(
    os.getenv(
        "GRAPH_TOKEN_CACHE_PATH",
        str(Path.home() / ".market_data" / "graph_delegated_token.json"),
    )
)

_ACCESS_TOKEN: Optional[str] = None
_ACCESS_TOKEN_EXPIRES_AT = 0.0


class GraphMailError(RuntimeError):
    """Raised when Microsoft Graph token or mail requests fail."""


@dataclass(frozen=True)
class GraphMessage:
    """Normalized Graph message fields used by the Bloomberg news collector."""

    graph_id: str
    sender: str
    subject: str
    body_preview: str
    received: str
    web_link: str

    def as_dict(self) -> dict:
        return {
            "graph_id": self.graph_id,
            "from": self.sender,
            "subject": self.subject,
            "body_preview": self.body_preview,
            "received": self.received,
            "web_link": self.web_link,
        }


def _read_config(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value.strip()
    try:
        return get_api_key(name).strip()
    except Exception as exc:
        raise GraphMailError(f"{name} is not configured") from exc


def _read_optional_config(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value.strip()
    try:
        return get_api_key(name).strip()
    except Exception:
        return default


def _token_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/token"


def _post_form(url: str, data: dict) -> dict:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return _open_json(request)


def _post_form_status(url: str, data: dict) -> tuple[int, dict]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise GraphMailError(f"Graph request failed: {exc}") from exc

    try:
        return status, json.loads(body)
    except json.JSONDecodeError as exc:
        raise GraphMailError(f"Graph returned invalid JSON: {body[:500]}") from exc


def _open_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GraphMailError(f"Graph HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GraphMailError(f"Graph request failed: {exc}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise GraphMailError(f"Graph returned invalid JSON: {body[:500]}") from exc


def get_access_token(force_refresh: bool = False) -> str:
    """Return a Graph access token, refreshing when near expiry."""

    mode = (
        _read_optional_config("BBG_GRAPH_AUTH_MODE")
        or _read_optional_config("GRAPH_AUTH_MODE")
        or "app"
    ).strip().lower()
    if mode in ("delegated", "device", "device_code", "device-code"):
        return _get_delegated_access_token(force_refresh=force_refresh)
    if mode not in ("app", "app_only", "app-only", "client_credentials", "client-credentials"):
        raise GraphMailError(f"Unsupported Graph auth mode: {mode}")
    return _get_app_access_token(force_refresh=force_refresh)


def _get_app_access_token(force_refresh: bool = False) -> str:
    """Return an app-only Graph access token, refreshing when near expiry."""

    global _ACCESS_TOKEN, _ACCESS_TOKEN_EXPIRES_AT

    now = time.time()
    if (
        not force_refresh
        and _ACCESS_TOKEN
        and now < _ACCESS_TOKEN_EXPIRES_AT - TOKEN_EXPIRY_SAFETY_SECONDS
    ):
        return _ACCESS_TOKEN

    tenant_id = _read_config("GRAPH_TENANT_ID")
    client_id = _read_config("GRAPH_CLIENT_ID")
    client_secret = _read_config("GRAPH_CLIENT_SECRET")

    payload = _post_form(
        _token_url(tenant_id),
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": GRAPH_SCOPE,
            "grant_type": "client_credentials",
        },
    )
    token = payload.get("access_token")
    if not token:
        raise GraphMailError(f"Graph token response missing access_token: {payload}")

    expires_in = int(payload.get("expires_in") or 3600)
    _ACCESS_TOKEN = str(token)
    _ACCESS_TOKEN_EXPIRES_AT = now + expires_in
    return _ACCESS_TOKEN


def _load_token_cache() -> dict:
    try:
        if DEFAULT_TOKEN_CACHE_PATH.exists():
            return json.loads(DEFAULT_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read Graph delegated token cache")
    return {}


def _save_token_cache(payload: dict) -> None:
    DEFAULT_TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = DEFAULT_TOKEN_CACHE_PATH.with_suffix(DEFAULT_TOKEN_CACHE_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(DEFAULT_TOKEN_CACHE_PATH)


def _cache_token_response(payload: dict) -> str:
    token = payload.get("access_token")
    if not token:
        raise GraphMailError(f"Graph token response missing access_token: {payload}")
    now = time.time()
    expires_in = int(payload.get("expires_in") or 3600)
    cache = {
        "access_token": str(token),
        "refresh_token": payload.get("refresh_token"),
        "expires_at": now + expires_in,
        "scope": payload.get("scope"),
        "token_type": payload.get("token_type"),
        "cached_at": now,
    }
    _save_token_cache(cache)
    return str(token)


def _refresh_delegated_token(
    token_url: str,
    client_id: str,
    refresh_token: str,
) -> Optional[str]:
    status, payload = _post_form_status(
        token_url,
        {
            "client_id": client_id,
            "scope": GRAPH_DELEGATED_SCOPE,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    if status == 200:
        return _cache_token_response(payload)
    logger.warning("Graph delegated refresh failed; starting device-code flow: %s", payload)
    return None


def _start_device_code_flow(tenant_id: str, client_id: str) -> dict:
    device_url = (
        f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/devicecode"
    )
    return _post_form(
        device_url,
        {
            "client_id": client_id,
            "scope": GRAPH_DELEGATED_SCOPE,
        },
    )


def _poll_device_code_token(
    token_url: str,
    client_id: str,
    device_code: str,
    interval: int,
    expires_in: int,
) -> str:
    deadline = time.time() + max(1, expires_in)
    poll_interval = max(1, interval)
    while time.time() < deadline:
        time.sleep(poll_interval)
        status, payload = _post_form_status(
            token_url,
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            },
        )
        if status == 200:
            return _cache_token_response(payload)

        error = payload.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            poll_interval += 5
            continue
        raise GraphMailError(f"Graph device-code authorization failed: {payload}")

    raise GraphMailError("Graph device-code authorization timed out")


def _get_delegated_access_token(force_refresh: bool = False) -> str:
    """Return a delegated Graph token using device-code auth and refresh cache."""

    tenant_id = _read_config("GRAPH_TENANT_ID")
    client_id = _read_config("GRAPH_CLIENT_ID")
    token_url = _token_url(tenant_id)

    cache = _load_token_cache()
    now = time.time()
    if (
        not force_refresh
        and cache.get("access_token")
        and now < float(cache.get("expires_at") or 0) - TOKEN_EXPIRY_SAFETY_SECONDS
    ):
        return str(cache["access_token"])

    refresh_token = cache.get("refresh_token")
    if refresh_token:
        token = _refresh_delegated_token(token_url, client_id, str(refresh_token))
        if token:
            return token

    flow = _start_device_code_flow(tenant_id, client_id)
    message = flow.get("message")
    if message:
        print(message, flush=True)
        logger.info("Graph delegated sign-in required: %s", message)
    else:
        print(
            "Open https://microsoft.com/devicelogin and enter code "
            f"{flow.get('user_code')}",
            flush=True,
        )
    return _poll_device_code_token(
        token_url=token_url,
        client_id=client_id,
        device_code=str(flow["device_code"]),
        interval=int(flow.get("interval") or 5),
        expires_in=int(flow.get("expires_in") or 900),
    )


def _get_json(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    return _open_json(request)


def _normalize_sender(sender: Optional[str]) -> str:
    return (sender or "").strip().lower()


def _sender_matches(sender: str, senders: Iterable[str]) -> bool:
    sender_norm = _normalize_sender(sender)
    if not sender_norm:
        return False
    for configured in senders:
        pattern = _normalize_sender(configured)
        if not pattern:
            continue
        if pattern.startswith("@"):
            if sender_norm.endswith(pattern):
                return True
        elif "@" in pattern:
            if sender_norm == pattern:
                return True
        elif sender_norm.endswith(f"@{pattern}") or sender_norm.endswith(pattern):
            return True
    return False


def _normalize_message(raw: dict) -> GraphMessage:
    from_obj = raw.get("from") or {}
    email_address = from_obj.get("emailAddress") or {}
    return GraphMessage(
        graph_id=str(raw.get("id") or ""),
        sender=str(email_address.get("address") or ""),
        subject=str(raw.get("subject") or ""),
        body_preview=str(raw.get("bodyPreview") or ""),
        received=str(raw.get("receivedDateTime") or ""),
        web_link=str(raw.get("webLink") or ""),
    )


def list_new_messages(
    mailbox: str,
    senders: Iterable[str],
    since_iso: str,
    top: int = 50,
) -> List[dict]:
    """
    Return new mailbox messages from configured senders since `since_iso`.

    Sender matching is applied client-side so exact addresses and domains are
    both supported while keeping the Graph query simple and reliable.
    """

    mailbox = mailbox.strip()
    if not mailbox:
        raise GraphMailError("mailbox is required")
    sender_list = [s.strip() for s in senders if s and s.strip()]
    if not sender_list:
        raise GraphMailError("at least one sender/domain is required")

    limit = max(1, min(int(top), 200))
    params = {
        "$select": "id,from,subject,bodyPreview,receivedDateTime,webLink",
        "$orderby": "receivedDateTime asc",
        "$top": str(min(limit, 100)),
        "$filter": f"receivedDateTime ge {since_iso}",
    }
    encoded_params = urllib.parse.urlencode(params, safe=",:")
    mode = (
        _read_optional_config("BBG_GRAPH_AUTH_MODE")
        or _read_optional_config("GRAPH_AUTH_MODE")
        or "app"
    ).strip().lower()
    if mode in ("delegated", "device", "device_code", "device-code"):
        url = f"{GRAPH_BASE_URL}/me/messages?{encoded_params}"
    else:
        url = (
            f"{GRAPH_BASE_URL}/users/{urllib.parse.quote(mailbox, safe='')}/messages"
            f"?{encoded_params}"
        )

    token = get_access_token()
    matched: List[GraphMessage] = []
    while url and len(matched) < limit:
        payload = _get_json(url, token)
        for raw in payload.get("value", []):
            message = _normalize_message(raw)
            if not message.graph_id:
                continue
            if _sender_matches(message.sender, sender_list):
                matched.append(message)
                if len(matched) >= limit:
                    break
        url = payload.get("@odata.nextLink") if len(matched) < limit else None

    logger.info("Fetched %s Bloomberg mail candidates from %s", len(matched), mailbox)
    return [message.as_dict() for message in matched]
