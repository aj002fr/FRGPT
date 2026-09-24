"""
Telegram Bot API notifier for one-way push alerts.

Uses stdlib-only HTTP (no external dependencies).
"""

import json
import logging
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Telegram Bot API base URL
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


def _load_telegram_config() -> Dict[str, str]:
    """Load Telegram-related config entries from config/keys.env."""
    keys_file = Path(__file__).parent.parent.parent / "config" / "keys.env"
    values: Dict[str, str] = {}

    if keys_file.exists():
        with open(keys_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip()
    return values


def _normalize_chat_id_value(value: Optional[str]) -> Optional[str]:
    """Return the first non-empty chat id from a raw config value."""
    if not value:
        return None
    parts = [part.strip() for part in str(value).split(",") if part.strip()]
    return parts[0] if parts else None


def load_telegram_chat_id(*preferred_keys: str) -> Optional[str]:
    """
    Resolve the first configured chat id from a list of keys.

    Example:
        load_telegram_chat_id("BBG_ALERT_CHAT_ID", "TELEGRAM_CHAT_ID")
    """
    config = _load_telegram_config()
    keys_to_try = preferred_keys or ("TELEGRAM_CHAT_ID",)
    for key in keys_to_try:
        value = _normalize_chat_id_value(config.get(key))
        if value:
            return value
    return None


def _load_telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    """
    Load Telegram bot token and default chat ID from config/keys.env.
    
    Returns:
        Tuple of (bot_token, default_chat_id)
    """
    config = _load_telegram_config()
    bot_token = config.get("TELEGRAM_BOT_TOKEN")
    chat_id = _normalize_chat_id_value(config.get("TELEGRAM_CHAT_ID"))
    return bot_token, chat_id


def send_telegram_message(
    text: str,
    chat_id: Optional[str] = None,
    token: Optional[str] = None,
    parse_mode: str = "HTML",
    disable_notification: bool = False,
) -> Dict[str, Any]:
    """
    Send a text message via Telegram Bot API.
    
    Args:
        text: Message text (supports HTML formatting if parse_mode="HTML")
        chat_id: Target chat/channel ID (uses default from keys.env if not provided)
        token: Bot token (uses TELEGRAM_BOT_TOKEN from keys.env if not provided)
        parse_mode: "HTML" or "Markdown" (default: HTML)
        disable_notification: Send silently (default: False)
        
    Returns:
        Dict with success status and response/error details
    """
    # Load credentials if not provided
    default_token, default_chat_id = _load_telegram_credentials()
    
    token = token or default_token
    chat_id = _normalize_chat_id_value(chat_id) or default_chat_id
    
    if not token:
        return {
            "success": False,
            "error": "No Telegram bot token provided. Set TELEGRAM_BOT_TOKEN in config/keys.env"
        }
    
    if not chat_id:
        return {
            "success": False,
            "error": "No chat ID provided. Set TELEGRAM_CHAT_ID in config/keys.env or pass chat_id parameter"
        }
    
    # Build API URL
    api_url = f"{TELEGRAM_API_BASE.format(token=token)}/sendMessage"
    
    # Build payload
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_notification": disable_notification,
    }
    
    try:
        data = json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(
            api_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            if result.get("ok"):
                logger.info(f"Telegram message sent to {chat_id}")
                return {
                    "success": True,
                    "message_id": result.get("result", {}).get("message_id"),
                    "chat_id": chat_id,
                }
            else:
                logger.warning(f"Telegram API error: {result}")
                return {
                    "success": False,
                    "error": result.get("description", "Unknown Telegram API error"),
                }
                
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else str(e)
        logger.error(f"Telegram HTTP error: {e.code} - {error_body}")
        return {
            "success": False,
            "error": f"HTTP {e.code}: {error_body}",
        }
    except urllib.error.URLError as e:
        logger.error(f"Telegram network error: {e.reason}")
        return {
            "success": False,
            "error": f"Network error: {e.reason}",
        }
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return {
            "success": False,
            "error": str(e),
        }


def send_telegram_photo(
    photo_path: str,
    caption: Optional[str] = None,
    chat_id: Optional[str] = None,
    token: Optional[str] = None,
    parse_mode: str = "HTML",
) -> Dict[str, Any]:
    """
    Send a photo via Telegram Bot API.
    
    Args:
        photo_path: Path to image file
        caption: Optional caption for the photo
        chat_id: Target chat/channel ID
        token: Bot token
        parse_mode: "HTML" or "Markdown" for caption formatting
        
    Returns:
        Dict with success status and response/error details
    """
    # Load credentials if not provided
    default_token, default_chat_id = _load_telegram_credentials()
    
    token = token or default_token
    chat_id = chat_id or default_chat_id
    
    if not token:
        return {
            "success": False,
            "error": "No Telegram bot token provided"
        }
    
    if not chat_id:
        return {
            "success": False,
            "error": "No chat ID provided"
        }
    
    photo_file = Path(photo_path)
    if not photo_file.exists():
        return {
            "success": False,
            "error": f"Photo file not found: {photo_path}"
        }
    
    # Build API URL
    api_url = f"{TELEGRAM_API_BASE.format(token=token)}/sendPhoto"
    
    # Build multipart form data
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    
    body_parts = []
    
    # Add chat_id
    body_parts.append(f'--{boundary}'.encode())
    body_parts.append(b'Content-Disposition: form-data; name="chat_id"')
    body_parts.append(b'')
    body_parts.append(chat_id.encode())
    
    # Add photo file
    body_parts.append(f'--{boundary}'.encode())
    body_parts.append(f'Content-Disposition: form-data; name="photo"; filename="{photo_file.name}"'.encode())
    body_parts.append(b'Content-Type: image/png')
    body_parts.append(b'')
    body_parts.append(photo_file.read_bytes())
    
    # Add caption if provided
    if caption:
        body_parts.append(f'--{boundary}'.encode())
        body_parts.append(b'Content-Disposition: form-data; name="caption"')
        body_parts.append(b'')
        body_parts.append(caption.encode())
        
        body_parts.append(f'--{boundary}'.encode())
        body_parts.append(b'Content-Disposition: form-data; name="parse_mode"')
        body_parts.append(b'')
        body_parts.append(parse_mode.encode())
    
    body_parts.append(f'--{boundary}--'.encode())
    
    body = b'\r\n'.join(body_parts)
    
    try:
        request = urllib.request.Request(
            api_url,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST"
        )
        
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            if result.get("ok"):
                logger.info(f"Telegram photo sent to {chat_id}")
                return {
                    "success": True,
                    "message_id": result.get("result", {}).get("message_id"),
                    "chat_id": chat_id,
                }
            else:
                logger.warning(f"Telegram API error: {result}")
                return {
                    "success": False,
                    "error": result.get("description", "Unknown Telegram API error"),
                }
                
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else str(e)
        logger.error(f"Telegram HTTP error: {e.code} - {error_body}")
        return {
            "success": False,
            "error": f"HTTP {e.code}: {error_body}",
        }
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return {
            "success": False,
            "error": str(e),
        }


def format_event_alert(
    event_name: str,
    event_time: str,
    actual: Optional[float] = None,
    consensus: Optional[float] = None,
    previous: Optional[float] = None,
    surprise: Optional[float] = None,
    percentile: Optional[float] = None,
    z_score: Optional[float] = None,
    symbol: Optional[str] = None,
    market_return_bps: Optional[float] = None,
    unit: str = "",
) -> str:
    """
    Format an economic event alert message for Telegram.
    
    Args:
        event_name: Name of the economic event
        event_time: Event timestamp
        actual: Actual released value
        consensus: Consensus/forecast value
        previous: Previous period value
        surprise: Actual - Consensus
        percentile: Percentile rank of surprise
        z_score: Z-score of surprise
        symbol: Market symbol analyzed
        market_return_bps: Market return in basis points
        unit: Unit suffix (e.g., "K", "%")
        
    Returns:
        Formatted HTML message string
    """
    # Determine surprise direction
    if surprise is not None:
        if surprise > 0:
            direction = "🟢 BEAT"
        elif surprise < 0:
            direction = "🔴 MISS"
        else:
            direction = "⚪ INLINE"
    else:
        direction = ""
    
    # Build message
    lines = [
        f"<b>📊 {event_name}</b>",
        f"<i>{event_time}</i>",
        "",
    ]
    
    # Data section
    if actual is not None:
        lines.append(f"<b>Actual:</b> {actual:.1f}{unit}")
    if consensus is not None:
        lines.append(f"<b>Consensus:</b> {consensus:.1f}{unit}")
    if previous is not None:
        lines.append(f"<b>Previous:</b> {previous:.1f}{unit}")
    
    # Surprise section
    if surprise is not None:
        lines.append("")
        sign = "+" if surprise > 0 else ""
        lines.append(f"<b>Surprise:</b> {sign}{surprise:.1f}{unit} {direction}")
    
    # Analysis section
    if percentile is not None or z_score is not None:
        lines.append("")
        lines.append("<b>Analysis:</b>")
        if percentile is not None:
            lines.append(f"  • Percentile: {percentile:.1f}%")
        if z_score is not None:
            lines.append(f"  • Z-Score: {z_score:+.2f}")
    
    # Market reaction section
    if symbol and market_return_bps is not None:
        lines.append("")
        lines.append(f"<b>{symbol} Reaction:</b> {market_return_bps:+.1f} bps")
    
    return "\n".join(lines)


def test_telegram_connection(
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Test Telegram bot connection by sending a test message.
    
    Args:
        token: Bot token (uses env if not provided)
        chat_id: Chat ID (uses env if not provided)
        
    Returns:
        Dict with success status and bot info
    """
    default_token, default_chat_id = _load_telegram_credentials()
    token = token or default_token
    chat_id = chat_id or default_chat_id
    
    if not token:
        return {
            "success": False,
            "error": "No bot token configured"
        }
    
    # Test getMe endpoint
    api_url = f"{TELEGRAM_API_BASE.format(token=token)}/getMe"
    
    try:
        request = urllib.request.Request(api_url, method="GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            if result.get("ok"):
                bot_info = result.get("result", {})
                return {
                    "success": True,
                    "bot_username": bot_info.get("username"),
                    "bot_id": bot_info.get("id"),
                    "chat_id_configured": chat_id is not None,
                }
            else:
                return {
                    "success": False,
                    "error": result.get("description", "Failed to get bot info"),
                }
                
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }

