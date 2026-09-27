"""Tiện ích xử lý văn bản, ngày tháng và HTML."""
import html
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Optional

from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401
    _HTML_PARSER = "lxml"
except ImportError:
    _HTML_PARSER = "html.parser"

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

_DATE_PATTERNS = [
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), ("d", "m", "y")),
    (re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})"), ("d", "m", "y")),
]
_RELATIVE_RE = re.compile(r"(\d+)\s*(giây|phút|giờ|ngày|tuần|tháng|năm)\s*trước", re.IGNORECASE)
_RELATIVE_DAYS = {"giây": 0, "phút": 0, "giờ": 0, "ngày": 1, "tuần": 7, "tháng": 30, "năm": 365}

_EMPLOYMENT_MAP = {
    "full_time": "Full-time", "full-time": "Full-time", "toàn thời gian": "Full-time",
    "part_time": "Part-time", "part-time": "Part-time", "bán thời gian": "Part-time",
    "contractor": "Contract", "temporary": "Contract", "hợp đồng": "Contract", "thời vụ": "Contract",
    "intern": "Internship", "thực tập": "Internship",
    "telecommute": "Remote", "từ xa": "Remote", "remote": "Remote",
    "hybrid": "Hybrid", "linh hoạt": "Hybrid",
    "văn phòng": "Onsite", "onsite": "Onsite",
}


def slugify(text: str) -> str:
    """'Kế toán tổng hợp' -> 'ke-toan-tong-hop' theo quy ước URL của TopCV."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def make_soup(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup, _HTML_PARSER)


def clean_text(node: Any) -> Optional[str]:
    """Chuyển node/chuỗi thành văn bản sạch, giữ xuống dòng giữa các khối."""
    if node is None:
        return None
    text = node.get_text("\n", strip=True) if hasattr(node, "get_text") else html.unescape(str(node))
    text = re.sub(r"[ \t\xa0\u200b]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text or None


def html_to_text(markup: Optional[str]) -> Optional[str]:
    if not markup:
        return None
    return clean_text(make_soup(html.unescape(markup)))


def parse_date(value: Any) -> Optional[str]:
    """Chuẩn hóa ngày về YYYY-MM-DD từ ISO hoặc dd/mm/yyyy."""
    if not value:
        return None
    text = str(value)
    for pattern, order in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        parts = dict(zip(order, map(int, match.groups())))
        try:
            return date(parts["y"], parts["m"], parts["d"]).isoformat()
        except ValueError:
            continue
    return None


def parse_relative_date(text: Optional[str], reference: datetime) -> Optional[str]:
    """Quy đổi chuỗi dạng 'Đăng 2 tuần trước' thành ngày tuyệt đối (ước lượng)."""
    if not text:
        return None
    match = _RELATIVE_RE.search(text)
    if match:
        days = int(match.group(1)) * _RELATIVE_DAYS[match.group(2).lower()]
        return (reference - timedelta(days=days)).date().isoformat()
    if re.search(r"hôm nay|vừa xong|vừa đăng", text, re.IGNORECASE):
        return reference.date().isoformat()
    return None


def normalize_employment_type(value: Optional[str]) -> Optional[str]:
    """Ánh xạ hình thức làm việc về nhãn chuẩn; giữ nguyên nếu không nhận diện được."""
    if not value:
        return None
    labels = []
    for part in re.split(r"[,;/|]", value):
        key = part.strip().lower()
        if not key:
            continue
        label = next((v for k, v in _EMPLOYMENT_MAP.items() if k in key), part.strip())
        if label not in labels:
            labels.append(label)
    return ", ".join(labels) or None


def as_text(value: Any) -> Optional[str]:
    """Rút chuỗi từ giá trị JSON-LD (chuỗi, danh sách hoặc object)."""
    if value is None or value == "":
        return None
    if isinstance(value, list):
        parts = [as_text(v) for v in value]
        return ", ".join(p for p in parts if p) or None
    if isinstance(value, dict):
        for key in ("name", "credentialCategory", "value", "text"):
            if value.get(key):
                return as_text(value[key])
        return None
    return clean_text(str(value))
