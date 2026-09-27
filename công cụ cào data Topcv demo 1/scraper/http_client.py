"""HTTP client bất đồng bộ có giới hạn đồng thời, giãn cách và retry."""
import asyncio
import logging
import random
from typing import Optional

import httpx

from config import ScraperSettings

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
]
_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}
_RETRY_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}


class AsyncFetcher:
    def __init__(self, settings: ScraperSettings):
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.concurrency)
        self._client: Optional[httpx.AsyncClient] = None
        # Trạng thái điều tiết dùng chung: khi bị 429, mọi request cùng tạm dừng và giãn cách thêm.
        self._pause_until = 0.0
        self._extra_delay = 0.0

    async def __aenter__(self) -> "AsyncFetcher":
        self._client = httpx.AsyncClient(
            headers=_BASE_HEADERS, timeout=self._settings.timeout, follow_redirects=True
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    async def get(self, url: str, params: Optional[dict] = None) -> Optional[str]:
        """Trả về HTML hoặc None nếu thất bại sau khi đã retry."""
        s = self._settings
        loop = asyncio.get_running_loop()
        for attempt in range(1, s.max_retries + 1):
            retry_after: Optional[float] = None
            async with self._semaphore:
                if (wait := self._pause_until - loop.time()) > 0:
                    await asyncio.sleep(wait)
                await asyncio.sleep(random.uniform(s.min_delay, s.max_delay) + self._extra_delay)
                try:
                    resp = await self._client.get(
                        url, params=params, headers={"User-Agent": random.choice(_USER_AGENTS)}
                    )
                except httpx.HTTPError as exc:
                    logger.warning("Lỗi kết nối %s (lần %d): %s", url, attempt, exc)
                else:
                    if resp.status_code == 200:
                        self._extra_delay = max(0.0, self._extra_delay - 0.1)
                        return resp.text
                    if resp.status_code not in _RETRY_STATUS:
                        logger.warning("Bỏ qua %s: HTTP %d", url, resp.status_code)
                        return None
                    logger.warning("HTTP %d tại %s (lần %d)", resp.status_code, url, attempt)
                    header = resp.headers.get("Retry-After", "")
                    retry_after = float(header) if header.isdigit() else None
                    if resp.status_code == 429:
                        self._extra_delay = min(self._extra_delay + 1.0, 8.0)
                        pause = retry_after or 10 * attempt
                        self._pause_until = max(self._pause_until, loop.time() + pause)
            # Chờ ngoài semaphore để không chặn các request khác.
            await asyncio.sleep(retry_after or (5 * 2 ** attempt + random.random()))
        logger.error("Thất bại sau %d lần thử: %s", s.max_retries, url)
        return None
