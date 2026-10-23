"""Client for Treasury FiscalData auction results."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Iterable, Optional

from .schema import (
    API_FIELDS,
    AUCTIONS_QUERY_PATH,
    AUCTION_TERM_MAP,
    AuctionTermConfig,
    FISCAL_DATA_BASE_URL,
)


class FiscalDataError(RuntimeError):
    """Raised when FiscalData cannot be queried successfully."""


def _build_url(params: dict[str, str]) -> str:
    query = urllib.parse.urlencode(params, safe=",:-")
    return f"{FISCAL_DATA_BASE_URL}{AUCTIONS_QUERY_PATH}?{query}"


def _request_json(url: str, timeout: int, retries: int) -> dict:
    headers = {
        "Accept": "application/json",
        "User-Agent": "market-data-puller-auction-data/1.0",
    }
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            return json.loads(body)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(min(2**attempt, 10))

    raise FiscalDataError(f"FiscalData request failed: {last_error}")


def fetch_auctions_for_term(
    term: AuctionTermConfig,
    start_date: Optional[date] = None,
    page_size: int = 100,
    timeout: int = 20,
    retries: int = 2,
) -> list[dict]:
    """Fetch auction rows for one coupon term."""

    filters = [
        f"security_type:eq:{term.security_type}",
        f"security_term:eq:{term.security_term}",
    ]
    if start_date is not None:
        filters.append(f"auction_date:gte:{start_date.isoformat()}")

    base_params = {
        "filter": ",".join(filters),
        "fields": ",".join(API_FIELDS),
        "sort": "-auction_date",
        "page[size]": str(page_size),
        "format": "json",
    }

    all_rows: list[dict] = []
    page_number = 1
    while True:
        params = dict(base_params)
        params["page[number]"] = str(page_number)
        payload = _request_json(_build_url(params), timeout=timeout, retries=retries)
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            raise FiscalDataError("FiscalData response did not include a data list")
        all_rows.extend(row for row in rows if isinstance(row, dict))

        meta = payload.get("meta", {})
        total_pages = int(meta.get("total-pages") or page_number)
        if page_number >= total_pages or not rows:
            break
        page_number += 1

    return all_rows


def fetch_recent_auctions(
    days_back: int = 14,
    terms: Optional[Iterable[AuctionTermConfig]] = None,
    timeout: int = 20,
    retries: int = 2,
) -> list[dict]:
    """Fetch recent auction rows for all configured coupon terms."""

    start_date = date.today() - timedelta(days=days_back)
    rows: list[dict] = []
    for term in terms or AUCTION_TERM_MAP:
        rows.extend(fetch_auctions_for_term(term, start_date=start_date, timeout=timeout, retries=retries))
    return rows

