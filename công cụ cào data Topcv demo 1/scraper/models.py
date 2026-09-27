"""Mô hình dữ liệu đầu ra và dữ liệu trung gian từ trang danh sách."""
from dataclasses import asdict, dataclass, fields
from typing import ClassVar, Optional


@dataclass
class ListingItem:
    """Thông tin sơ bộ của một tin lấy từ trang danh sách."""

    job_id: str
    url: str
    title: Optional[str] = None
    company: Optional[str] = None
    date_hint: Optional[str] = None
    search_group: Optional[str] = None
    search_category: Optional[str] = None


@dataclass
class JobPosting:
    """Bản ghi tin tuyển dụng theo đúng schema đầu ra (thứ tự trường giữ nguyên)."""

    job_id: str
    source: str
    source_url: str
    archive_source: Optional[str] = None
    posting_date: Optional[str] = None
    deadline: Optional[str] = None
    retrieval_date: Optional[str] = None
    job_title_raw: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    salary: Optional[str] = None
    experience_required: Optional[str] = None
    education_required: Optional[str] = None
    employment_type: Optional[str] = None
    job_description: Optional[str] = None
    job_requirements: Optional[str] = None
    job_benefits: Optional[str] = None
    category_raw: Optional[str] = None
    industry_raw: Optional[str] = None

    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = (
        "job_id", "source", "source_url", "posting_date",
        "retrieval_date", "job_title_raw", "company",
    )

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def missing_fields(self) -> list[str]:
        return [name for name in self.REQUIRED_FIELDS if not getattr(self, name)]

    def to_dict(self) -> dict:
        return asdict(self)
