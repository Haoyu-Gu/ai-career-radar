import re
from typing import Any

import httpx

from ..db import connect, utcnow
from ..settings import settings


ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


def current_usd_cny() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT rate, effective_date, retrieved_at, source_url
            FROM exchange_rates
            WHERE base_currency='USD' AND quote_currency='CNY'
            ORDER BY effective_date DESC, id DESC LIMIT 1
            """
        ).fetchone()
    if row:
        return {**dict(row), "base_currency": "USD", "quote_currency": "CNY", "is_fallback": False}
    return {
        "rate": settings.usd_cny_fallback,
        "effective_date": settings.usd_cny_fallback_date,
        "retrieved_at": None,
        "source_url": ECB_DAILY_URL,
        "base_currency": "USD",
        "quote_currency": "CNY",
        "is_fallback": True,
    }


async def refresh_usd_cny() -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": "AITrendRadar/0.2 (personal research dashboard)"},
    ) as client:
        response = await client.get(ECB_DAILY_URL)
        response.raise_for_status()
    text = response.text
    date_match = re.search(r"<Cube time='([^']+)'", text)
    usd_match = re.search(r"currency='USD' rate='([^']+)'", text)
    cny_match = re.search(r"currency='CNY' rate='([^']+)'", text)
    if not (date_match and usd_match and cny_match):
        raise RuntimeError("ECB 汇率文件中没有找到 USD/CNY 数据")
    # ECB publishes both currencies per EUR, so CNY per USD is CNY/EUR divided by USD/EUR.
    rate = float(cny_match.group(1)) / float(usd_match.group(1))
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO exchange_rates(base_currency, quote_currency, rate, effective_date,
              retrieved_at, source_url) VALUES('USD', 'CNY', ?, ?, ?, ?)
            ON CONFLICT(base_currency, quote_currency, effective_date) DO UPDATE SET
              rate=excluded.rate, retrieved_at=excluded.retrieved_at, source_url=excluded.source_url
            """,
            (rate, date_match.group(1), utcnow(), ECB_DAILY_URL),
        )
    return current_usd_cny()


def to_cny(value: float | None, currency: str | None) -> float | None:
    if value is None:
        return None
    if currency == "CNY":
        return float(value)
    if currency == "USD":
        return float(value) * float(current_usd_cny()["rate"])
    return None
