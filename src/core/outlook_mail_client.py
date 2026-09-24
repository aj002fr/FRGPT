"""
Local Outlook Desktop mail client for Bloomberg news ingestion.

Reads the primary Outlook profile Inbox via Windows COM. This avoids Microsoft
Graph admin consent while keeping the downstream Telegram approval flow the same.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, List, Optional

logger = logging.getLogger(__name__)

OL_FOLDER_INBOX = 6


class OutlookMailError(RuntimeError):
    """Raised when Outlook Desktop COM access fails."""


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


def _parse_since(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_utc(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.astimezone(timezone.utc)


def _format_utc(value: Optional[datetime]) -> str:
    if not value:
        return ""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_outlook_restrict_datetime(value: datetime) -> str:
    local_dt = value.astimezone()
    return local_dt.strftime("%m/%d/%Y %I:%M %p")


def _get_sender_address(item) -> str:
    for attr in ("SenderEmailAddress", "SentOnBehalfOfName", "SenderName"):
        try:
            value = getattr(item, attr, None)
            if value and "@" in str(value):
                return str(value)
        except Exception:
            continue

    try:
        sender = item.Sender
        if sender:
            exchange_user = sender.GetExchangeUser()
            if exchange_user and exchange_user.PrimarySmtpAddress:
                return str(exchange_user.PrimarySmtpAddress)
    except Exception:
        pass

    try:
        return str(item.SenderName or "")
    except Exception:
        return ""


def _message_from_item(item, senders: Iterable[str]) -> Optional[dict]:
    sender = _get_sender_address(item)
    if not _sender_matches(sender, senders):
        return None
    entry_id = str(getattr(item, "EntryID", "") or "")
    if not entry_id:
        return None
    received_dt = _to_utc(getattr(item, "ReceivedTime", None))
    subject = str(getattr(item, "Subject", "") or "")
    body = str(getattr(item, "Body", "") or "")
    return {
        "graph_id": f"outlook:{entry_id}",
        "from": sender,
        "subject": subject,
        "body_preview": body[:4000],
        "received": _format_utc(received_dt),
        "web_link": "",
    }


def _get_running_outlook(win32com_client):
    try:
        return win32com_client.GetActiveObject("Outlook.Application")
    except Exception as exc:
        if os.getenv("BBG_OUTLOOK_START_IF_CLOSED", "").strip().lower() in ("1", "true", "yes"):
            logger.info("No running Outlook instance found; starting Outlook via COM")
            return win32com_client.Dispatch("Outlook.Application")
        raise OutlookMailError(
            "Outlook Desktop is not running or is not registered with COM. "
            "Open Outlook Desktop, wait for the Inbox to load, then rerun."
        ) from exc


def list_new_messages(
    mailbox: str,
    senders: Iterable[str],
    since_iso: str,
    top: int = 50,
) -> List[dict]:
    """
    Return matching Outlook Inbox messages newer than `since_iso`.

    `mailbox` is accepted for interface parity with the Graph client. The local
    Outlook profile determines the actual mailbox; this function reads the
    primary Inbox configured in Outlook Desktop.
    """

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise OutlookMailError("pywin32/win32com is required for Outlook COM access") from exc

    sender_list = [s.strip() for s in senders if s and s.strip()]
    if not sender_list:
        raise OutlookMailError("at least one sender/domain is required")

    since_dt = _parse_since(since_iso)
    limit = max(1, min(int(top), 200))
    max_checked = max(500, limit * 25)

    pythoncom.CoInitialize()
    try:
        logger.info("Attaching to running Outlook Desktop via COM")
        outlook = _get_running_outlook(win32com.client)
        namespace = outlook.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(OL_FOLDER_INBOX)
        items = inbox.Items
        restriction = f"[ReceivedTime] >= '{_format_outlook_restrict_datetime(since_dt)}'"
        try:
            items = items.Restrict(restriction)
            logger.info("Applied Outlook Inbox restriction: %s", restriction)
        except Exception:
            logger.exception("Failed to apply Outlook restriction; falling back to sorted Inbox")
        items.Sort("[ReceivedTime]", True)

        matched: List[dict] = []
        checked = 0
        for item in items:
            checked += 1
            if checked > max_checked:
                logger.warning("Stopped Outlook scan after %s items", max_checked)
                break
            try:
                received_dt = _to_utc(getattr(item, "ReceivedTime", None))
                if received_dt and received_dt < since_dt:
                    break
                message = _message_from_item(item, sender_list)
                if not message:
                    continue
                matched.append(message)
                if len(matched) >= limit:
                    break
            except Exception:
                logger.exception("Failed to normalize Outlook message")
                continue

        logger.info(
            "Fetched %s Bloomberg mail candidates from Outlook Inbox after checking %s items",
            len(matched),
            checked,
        )
        return list(reversed(matched))
    except Exception as exc:
        raise OutlookMailError(f"Outlook COM read failed: {exc}") from exc
    finally:
        pythoncom.CoUninitialize()


class _InboxItemsEvents:
    _senders: List[str] = []
    _on_message: Optional[Callable[[dict], None]] = None

    def OnItemAdd(self, item) -> None:
        try:
            message = _message_from_item(item, self._senders)
            if message and self._on_message:
                logger.info("Outlook ItemAdd matched Bloomberg mail: %s", message.get("subject"))
                self._on_message(message)
        except Exception:
            logger.exception("Failed to handle Outlook ItemAdd event")


def watch_inbox(
    senders: Iterable[str],
    on_message: Callable[[dict], None],
    stop_event,
    pump_interval: float = 0.5,
) -> None:
    """Watch the primary Outlook Inbox and call `on_message` for matching new mail."""

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise OutlookMailError("pywin32/win32com is required for Outlook COM access") from exc

    sender_list = [s.strip() for s in senders if s and s.strip()]
    if not sender_list:
        raise OutlookMailError("at least one sender/domain is required")

    pythoncom.CoInitialize()
    try:
        logger.info("Starting Outlook Inbox ItemAdd listener")
        outlook = _get_running_outlook(win32com.client)
        namespace = outlook.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(OL_FOLDER_INBOX)
        items = inbox.Items
        handler = win32com.client.WithEvents(items, _InboxItemsEvents)
        handler._senders = sender_list
        handler._on_message = on_message

        logger.info("Outlook Inbox ItemAdd listener attached")
        while not stop_event.is_set():
            pythoncom.PumpWaitingMessages()
            time.sleep(max(0.05, float(pump_interval)))
    except Exception as exc:
        raise OutlookMailError(f"Outlook COM watch failed: {exc}") from exc
    finally:
        pythoncom.CoUninitialize()
