"""Giao diện chung cho mọi nguồn (TopCV, VietnamWorks, ITviec...)."""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from scraper.models import JobPosting, ListingItem


class BaseSource(ABC):
    name: str = ""

    @abstractmethod
    def build_listing_url(self, keyword: str, page: int, extra_params: dict) -> tuple[str, dict]:
        """Trả về (url, params) cho trang danh sách."""

    @abstractmethod
    def parse_listing(self, html: str, retrieved_at: datetime) -> list[ListingItem]:
        """Trích danh sách tin từ trang kết quả tìm kiếm."""

    @abstractmethod
    def parse_detail(self, html: str, item: ListingItem, retrieved_at: datetime) -> Optional[JobPosting]:
        """Trích bản ghi đầy đủ từ trang chi tiết."""
