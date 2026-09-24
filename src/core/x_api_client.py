"""
Stdlib-only client for X API v2 filtered stream rules + stream consumption.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config.settings import get_api_key

logger = logging.getLogger(__name__)

X_API_BASE = "https://api.x.com/2"
STREAM_RULES_URL = f"{X_API_BASE}/tweets/search/stream/rules"
STREAM_URL = f"{X_API_BASE}/tweets/search/stream"
USER_LOOKUP_URL = f"{X_API_BASE}/users/by"


def _format_http_error(exc: HTTPError) -> str:
    detail = exc.read().decode("utf-8", errors="replace").strip()
    header_keys = (
        "retry-after",
        "x-rate-limit-limit",
        "x-rate-limit-remaining",
        "x-rate-limit-reset",
        "x-connection-hash",
    )
    header_parts = []
    for key in header_keys:
        value = exc.headers.get(key)
        if value:
            header_parts.append(f"{key}={value}")

    message = f"HTTP {exc.code}"
    if exc.reason:
        message += f" {exc.reason}"
    if header_parts:
        message += f" [{' ; '.join(header_parts)}]"
    if detail:
        message += f": {detail}"
    return message


@dataclass(frozen=True)
class StreamRule:
    value: str
    tag: str
    rule_id: Optional[str] = None


def _request_json(
    method: str,
    url: str,
    bearer_token: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {bearer_token}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, headers=headers, data=data, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raise RuntimeError(_format_http_error(exc)) from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc


def get_bearer_token(override: Optional[str] = None) -> str:
    return override or get_api_key("X_BEARER_TOKEN")


def list_stream_rules(bearer_token: str) -> List[StreamRule]:
    payload = _request_json("GET", STREAM_RULES_URL, bearer_token)
    data = payload.get("data") or []
    rules: List[StreamRule] = []
    for row in data:
        rules.append(
            StreamRule(
                value=row.get("value", ""),
                tag=row.get("tag", ""),
                rule_id=row.get("id"),
            )
        )
    return rules


def add_stream_rules(rules: Iterable[StreamRule], bearer_token: str) -> Dict[str, Any]:
    add_payload = [{"value": r.value, "tag": r.tag} for r in rules]
    if not add_payload:
        return {"success": True, "added": 0}
    return _request_json("POST", STREAM_RULES_URL, bearer_token, payload={"add": add_payload})


def delete_stream_rules(rule_ids: Iterable[str], bearer_token: str) -> Dict[str, Any]:
    ids = [rid for rid in rule_ids if rid]
    if not ids:
        return {"success": True, "deleted": 0}
    return _request_json(
        "POST",
        STREAM_RULES_URL,
        bearer_token,
        payload={"delete": {"ids": ids}},
    )


def delete_rules_by_tag_prefix(tag_prefix: str, bearer_token: str) -> int:
    rules = list_stream_rules(bearer_token)
    to_delete = [r.rule_id for r in rules if r.tag and r.tag.startswith(tag_prefix)]
    delete_stream_rules(to_delete, bearer_token)
    return len([rid for rid in to_delete if rid])


def resolve_usernames(
    usernames: Iterable[str],
    bearer_token: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Resolve usernames to user objects. Returns (found, missing)."""
    usernames_list = [u.strip().lstrip("@") for u in usernames if u.strip()]
    found: List[Dict[str, Any]] = []
    missing: List[str] = []

    for i in range(0, len(usernames_list), 100):
        chunk = usernames_list[i : i + 100]
        params = {
            "user.fields": "id,name,username,verified,created_at",
            "usernames": ",".join(chunk),
        }
        url = f"{USER_LOOKUP_URL}?{urlencode(params)}"
        payload = _request_json("GET", url, bearer_token)
        found.extend(payload.get("data") or [])
        errors = payload.get("errors") or []
        for err in errors:
            if "value" in err:
                missing.append(err["value"])
    return found, missing


def open_stream(
    bearer_token: str,
    params: Optional[Dict[str, str]] = None,
    timeout: int = 90,
):
    query = urlencode(params or {})
    url = f"{STREAM_URL}?{query}" if query else STREAM_URL
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "User-Agent": "x-macro-stream-bot",
    }
    request = Request(url, headers=headers, method="GET")
    try:
        return urlopen(request, timeout=timeout)
    except HTTPError as exc:
        raise RuntimeError(_format_http_error(exc)) from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc


def iter_stream_events(response) -> Iterable[Dict[str, Any]]:
    """Yield JSON events from a streaming HTTP response."""
    while True:
        line = response.readline()
        if not line:
            break
        if line in (b"\r\n", b"\n"):
            continue
        try:
            payload = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON stream line: %r", line[:200])
            continue
        yield payload
