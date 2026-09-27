"""Cấu hình trung tâm: danh mục mục tiêu, từ khóa tìm kiếm và tham số vận hành."""
from dataclasses import dataclass, field

# Nhóm -> Danh mục -> danh sách slug tìm kiếm trên TopCV (tim-viec-lam-<slug>).
TARGET_CATEGORIES: dict[str, dict[str, list[str]]] = {
    "Business": {
        "Marketing": ["marketing", "digital-marketing", "content-marketing", "seo"],
        "Finance/Accounting": ["ke-toan", "tai-chinh", "ke-toan-tong-hop", "kiem-toan"],
        "HR": ["nhan-su", "tuyen-dung", "hanh-chinh-nhan-su", "c-b"],
        "Sales": ["kinh-doanh", "sales", "sales-b2b", "tu-van-ban-hang"],
        "Business Analysis": ["business-analyst", "phan-tich-kinh-doanh", "phan-tich-nghiep-vu"],
        "Operations": ["van-hanh", "operations", "chuoi-cung-ung", "logistics"],
    },
    "IT": {
        "Software Development": ["lap-trinh-vien", "software-engineer", "backend-developer",
                                 "frontend-developer", "mobile-developer"],
        "Data/AI": ["data-analyst", "data-engineer", "ai-engineer", "machine-learning", "data-scientist"],
        "QA": ["tester", "qa-qc-phan-mem", "automation-test", "qa-engineer"],
        "DevOps/Cloud": ["devops", "cloud-engineer", "system-engineer", "sre"],
        "Cybersecurity": ["an-ninh-mang", "bao-mat-thong-tin", "security-engineer", "pentest"],
        "IT Support": ["it-support", "it-helpdesk", "it-phan-cung-mang", "quan-tri-he-thong"],
    },
}


@dataclass
class ScraperSettings:
    """Tham số vận hành của pipeline, có thể ghi đè qua CLI."""

    target_min: int = 3000
    target_max: int = 5000
    lookback_days: int = 365
    concurrency: int = 3
    min_delay: float = 1.0
    max_delay: float = 2.5
    max_retries: int = 6
    timeout: float = 30.0
    max_pages_per_keyword: int = 40
    # Số trang liên tiếp không thu được bản ghi mới trước khi bỏ từ khóa.
    max_stale_pages: int = 3
    output_dir: str = "output"
    listing_extra_params: dict[str, str] = field(default_factory=lambda: {"sort": "new"})
