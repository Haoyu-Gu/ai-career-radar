import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from ..settings import settings


def _retry_delay(value: str | None, fallback: float) -> float:
    try:
        return min(max(float(value or fallback), 0.0), 60.0)
    except ValueError:
        return fallback


class CachedHttpClient:
    def __init__(self, namespace: str, min_interval: float = 0.4):
        self.namespace = namespace
        self.min_interval = min_interval
        self._last_request = 0.0
        self.cache_dir = settings.raw_dir.parent / "cache" / namespace
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "AITrendRadar/0.1 (+personal research; respectful metadata client)"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def get_json(
        self, url: str, params: dict[str, Any] | None = None, cache_ttl_seconds: int = 1800
    ) -> tuple[Any, httpx.Headers, bool]:
        cache_key = hashlib.sha256(
            (url + "?" + json.dumps(params or {}, sort_keys=True)).encode()
        ).hexdigest()
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists() and cache_ttl_seconds > 0:
            age = asyncio.get_running_loop().time() - cache_path.stat().st_mtime
            # monotonic and mtime have different origins; use wall-clock through file metadata only below.
            import time
            if time.time() - cache_path.stat().st_mtime < cache_ttl_seconds:
                return json.loads(cache_path.read_text(encoding="utf-8")), httpx.Headers(), True

        loop = asyncio.get_running_loop()
        wait = max(0.0, self.min_interval - (loop.time() - self._last_request))
        if wait:
            await asyncio.sleep(wait)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.get(url, params=params)
                self._last_request = loop.time()
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    await asyncio.sleep(_retry_delay(retry_after, 2 ** (attempt + 1)))
                    continue
                response.raise_for_status()
                payload = response.json()
                cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                return payload, response.headers, False
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        raise RuntimeError(f"GET failed after retries: {url}: {last_error}")

    async def post_json(
        self, url: str, body: dict[str, Any], cache_ttl_seconds: int = 1800,
        headers: dict[str, str] | None = None,
    ) -> tuple[Any, httpx.Headers, bool]:
        cache_key = hashlib.sha256(
            (url + "#" + json.dumps(body, sort_keys=True, ensure_ascii=False)).encode()
        ).hexdigest()
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists() and cache_ttl_seconds > 0:
            import time
            if time.time() - cache_path.stat().st_mtime < cache_ttl_seconds:
                return json.loads(cache_path.read_text(encoding="utf-8")), httpx.Headers(), True

        loop = asyncio.get_running_loop()
        wait = max(0.0, self.min_interval - (loop.time() - self._last_request))
        if wait:
            await asyncio.sleep(wait)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.post(url, json=body, headers=headers)
                self._last_request = loop.time()
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    await asyncio.sleep(_retry_delay(retry_after, 2 ** (attempt + 1)))
                    continue
                response.raise_for_status()
                payload = response.json()
                cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                return payload, response.headers, False
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        raise RuntimeError(f"POST failed after retries: {url}: {last_error}")
