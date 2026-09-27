"""Điều phối pipeline theo vòng lặp Analyze -> Plan -> Act -> Observe."""
import asyncio
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from config import ScraperSettings
from scraper.db import PostgresStorage
from scraper.http_client import AsyncFetcher
from scraper.models import JobPosting, ListingItem
from scraper.sources.base import BaseSource
from scraper.storage import DatasetStorage
from scraper.utils import DATETIME_FORMAT, slugify

logger = logging.getLogger(__name__)


@dataclass
class CategoryTask:
    group: str
    category: str
    keywords: list[str]
    quota: int


@dataclass
class PipelineState:
    started_at: datetime
    seen_ids: set[str] = field(default_factory=set)
    fingerprints: set[str] = field(default_factory=set)
    records: list[dict] = field(default_factory=list)
    per_category: Counter = field(default_factory=Counter)
    stats: Counter = field(default_factory=Counter)
    next_page: dict[tuple[str, str], int] = field(default_factory=dict)
    done_keywords: set[tuple[str, str]] = field(default_factory=set)
    exhausted: set[str] = field(default_factory=set)


class ScrapePipeline:
    def __init__(self, source: BaseSource, settings: ScraperSettings,
                 categories: dict[str, dict[str, list[str]]], storage: DatasetStorage,
                 db: Optional[PostgresStorage] = None):
        self.source = source
        self.settings = settings
        self.categories = categories
        self.storage = storage
        self.db = db

    async def run(self) -> list[dict]:
        state = self._analyze()
        tasks = self._plan(state)
        async with AsyncFetcher(self.settings) as fetcher:
            while tasks and len(state.records) < self.settings.target_max:
                for task in tasks:
                    if len(state.records) >= self.settings.target_max:
                        break
                    await self._act(fetcher, task, state)
                tasks = self._rebalance(tasks, state)
        return self._finalize(state)

    # ---------- Analyze: khôi phục trạng thái từ checkpoint ----------

    def _analyze(self) -> PipelineState:
        state = PipelineState(started_at=datetime.now())
        for category, record in self.storage.load_checkpoint():
            if record["job_id"] in state.seen_ids or not self._is_recent(record):
                continue
            state.seen_ids.add(record["job_id"])
            fingerprint = self._fingerprint(record)
            if fingerprint in state.fingerprints:
                continue
            state.fingerprints.add(fingerprint)
            state.records.append(record)
            state.per_category[category] += 1
        state.seen_ids |= self.storage.load_rejected()
        if state.records:
            logger.info("Khôi phục %d bản ghi từ checkpoint.", len(state.records))
            if self.db:
                # Đồng bộ các bản ghi có thể chưa vào DB ở lần chạy trước.
                logger.info("Đồng bộ %d bản ghi checkpoint vào PostgreSQL.", self.db.upsert_many(state.records))
        return state

    # ---------- Plan: chia hạn mức đều cho các danh mục ----------

    def _plan(self, state: PipelineState) -> list[CategoryTask]:
        pairs = [(g, c, kws) for g, cats in self.categories.items() for c, kws in cats.items()]
        quota = math.ceil(self.settings.target_max / max(len(pairs), 1))
        tasks = [CategoryTask(g, c, kws, quota) for g, c, kws in pairs]
        logger.info("Kế hoạch: %d danh mục, hạn mức ban đầu %d tin/danh mục.", len(tasks), quota)
        return tasks

    def _rebalance(self, tasks: list[CategoryTask], state: PipelineState) -> list[CategoryTask]:
        """Chuyển phần hạn mức còn thiếu sang các danh mục chưa cạn tin."""
        remaining = self.settings.target_max - len(state.records)
        open_tasks = [t for t in tasks if t.category not in state.exhausted]
        if remaining <= 0 or not open_tasks:
            return []
        extra = math.ceil(remaining / len(open_tasks))
        for task in open_tasks:
            task.quota = state.per_category[task.category] + extra
        logger.info("Phân bổ lại: còn thiếu %d tin, thêm %d tin cho mỗi danh mục chưa cạn: %s.",
                    remaining, extra, ", ".join(t.category for t in open_tasks))
        return open_tasks

    # ---------- Act: duyệt trang danh sách và tải chi tiết song song ----------

    async def _act(self, fetcher: AsyncFetcher, task: CategoryTask, state: PipelineState) -> None:
        if self._task_full(task, state):
            return
        logger.info("[%s / %s] Bắt đầu (đã có %d/%d).", task.group, task.category,
                    state.per_category[task.category], task.quota)
        for keyword in task.keywords:
            key = (task.category, keyword)
            if key in state.done_keywords:
                continue
            stale_pages, previous_ids = 0, None
            page = state.next_page.get(key, 1)
            while page <= self.settings.max_pages_per_keyword:
                if self._task_full(task, state):
                    state.next_page[key] = page
                    return
                url, params = self.source.build_listing_url(keyword, page, self.settings.listing_extra_params)
                html = await fetcher.get(url, params)
                items = self.source.parse_listing(html, state.started_at) if html else []
                page_ids = {i.job_id for i in items}
                # Vượt quá số trang thực, TopCV có thể trả lại trang cuối lặp lại.
                if not items or page_ids == previous_ids:
                    logger.info("  '%s' hết kết quả ở trang %d.", keyword, page)
                    break
                previous_ids = page_ids

                new_items = [i for i in items if i.job_id not in state.seen_ids]
                for item in new_items:
                    item.search_group, item.search_category = task.group, task.category
                    state.seen_ids.add(item.job_id)

                accepted = 0
                pending = list(new_items)
                # Tải theo đợt vừa đủ số còn thiếu để không tốn request khi sắp đạt hạn mức.
                while pending and not self._task_full(task, state):
                    remaining = min(task.quota - state.per_category[task.category],
                                    self.settings.target_max - len(state.records))
                    batch_size = max(remaining, self.settings.concurrency)
                    batch, pending = pending[:batch_size], pending[batch_size:]
                    postings = await asyncio.gather(*(self._fetch_detail(fetcher, i, state) for i in batch))
                    accepted += self._observe(postings, task, state)
                for item in pending:
                    state.seen_ids.discard(item.job_id)
                logger.info("  '%s' trang %d: %d tin, %d mới, nhận %d (danh mục %d/%d, tổng %d).",
                            keyword, page, len(items), len(new_items), accepted,
                            state.per_category[task.category], task.quota, len(state.records))
                if pending:
                    # Đủ hạn mức giữa trang: lần phân bổ sau quay lại đúng trang này.
                    state.next_page[key] = page
                    return
                page += 1

                # Trang toàn tin đã thấy (khi chạy tiếp từ checkpoint) không tính là trang hết tin.
                if new_items:
                    stale_pages = 0 if accepted else stale_pages + 1
                if stale_pages >= self.settings.max_stale_pages:
                    logger.info("  '%s' dừng: %d trang liên tiếp không có tin hợp lệ mới.", keyword, stale_pages)
                    break
            state.done_keywords.add(key)
        if not self._task_full(task, state):
            state.exhausted.add(task.category)
            logger.info("[%s] Đã cạn tin (%d bản ghi).", task.category, state.per_category[task.category])

    async def _fetch_detail(self, fetcher: AsyncFetcher, item: ListingItem,
                            state: PipelineState) -> Optional[JobPosting]:
        html = await fetcher.get(item.url)
        if not html:
            state.stats["fetch_failed"] += 1
            return None
        try:
            return self.source.parse_detail(html, item, datetime.now())
        except Exception:
            logger.exception("Lỗi phân tích %s", item.url)
            state.stats["parse_failed"] += 1
            return None

    # ---------- Observe: kiểm định, lọc thời gian, lưu checkpoint ----------

    def _observe(self, postings: list[Optional[JobPosting]], task: CategoryTask, state: PipelineState) -> int:
        accepted = 0
        for posting in postings:
            if posting is None:
                continue
            if missing := posting.missing_fields():
                logger.debug("Thiếu trường %s: %s", missing, posting.source_url)
                state.stats["invalid"] += 1
                self.storage.append_rejected(posting.job_id)
                continue
            record = posting.to_dict()
            if not self._is_recent(record):
                state.stats["too_old"] += 1
                self.storage.append_rejected(posting.job_id)
                continue
            fingerprint = self._fingerprint(record)
            if fingerprint in state.fingerprints:
                state.stats["duplicate"] += 1
                self.storage.append_rejected(posting.job_id)
                continue
            if self._task_full(task, state):
                # Trả lại tin cho danh mục khác có thể thu thập.
                state.seen_ids.discard(posting.job_id)
                continue
            state.fingerprints.add(fingerprint)
            state.records.append(record)
            state.per_category[task.category] += 1
            self.storage.append_checkpoint(task.category, record)
            if self.db and not self.db.upsert(record):
                state.stats["db_failed"] += 1
            accepted += 1
        return accepted

    def _task_full(self, task: CategoryTask, state: PipelineState) -> bool:
        return (state.per_category[task.category] >= task.quota
                or len(state.records) >= self.settings.target_max)

    @staticmethod
    def _fingerprint(record: dict) -> str:
        """Cùng tiêu đề + công ty + địa điểm được coi là một tin đăng lại."""
        return "|".join(slugify(record.get(f) or "") for f in ("job_title_raw", "company", "location"))

    def _is_recent(self, record: dict) -> bool:
        """Chỉ giữ tin đăng trong khoảng lookback tính từ retrieval_date của chính bản ghi."""
        try:
            retrieved = datetime.strptime(record["retrieval_date"], DATETIME_FORMAT)
            posted = datetime.strptime(record["posting_date"], "%Y-%m-%d")
        except (KeyError, TypeError, ValueError):
            return False
        return posted.date() >= (retrieved - timedelta(days=self.settings.lookback_days)).date()

    def _finalize(self, state: PipelineState) -> list[dict]:
        paths = self.storage.export(state.records)
        logger.info("Hoàn tất: %d bản ghi hợp lệ (đã lọc trùng). Thống kê loại bỏ: %s",
                    len(state.records), dict(state.stats))
        for category, count in sorted(state.per_category.items()):
            logger.info("  %-22s %d", category, count)
        logger.info("Đã xuất: %s | %s", paths["json"], paths["csv"])
        if len(state.records) < self.settings.target_min:
            logger.warning("Chưa đạt mục tiêu tối thiểu %d bản ghi; hãy chạy lại để tiếp tục từ checkpoint "
                           "hoặc bổ sung từ khóa trong config.py.", self.settings.target_min)
        return state.records
