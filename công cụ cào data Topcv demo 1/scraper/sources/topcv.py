"""Parser TopCV: ưu tiên JSON-LD (JobPosting), dự phòng bằng CSS selector và tiêu đề mục."""
import json
import re
from datetime import datetime
from typing import Any, Iterator, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from scraper.models import JobPosting, ListingItem
from scraper.sources.base import BaseSource
from scraper.utils import (
    DATETIME_FORMAT, as_text, clean_text, html_to_text, make_soup,
    normalize_employment_type, parse_date, parse_relative_date,
)

BASE_URL = "https://www.topcv.vn"
_JOB_PATH_RE = re.compile(
    r"/(?:viec-lam/[^/?#]+/(\d+)\.html|brand/[^/?#]+/tuyen-dung/[^/?#]+-j(\d+)\.html)"
)
_HEADING_TAGS = ["h2", "h3", "h4"]
# Thứ tự quan trọng: kiểm tra "quyền lợi"/"yêu cầu" trước "mô tả".
_SECTION_KEYWORDS = [
    ("benefits", ("quyền lợi", "phúc lợi", "benefit")),
    ("requirements", ("yêu cầu ứng viên", "yêu cầu công việc", "yêu cầu", "requirement")),
    ("description", ("mô tả công việc", "mô tả", "job description")),
]
# (container, nhãn, giá trị) cho các khối thông tin dạng "nhãn: giá trị".
_LABELED_BLOCKS = [
    (".job-detail__info--section", ".job-detail__info--section-content-title",
     ".job-detail__info--section-content-value"),
    (".box-general-group-info", ".box-general-group-info-title", ".box-general-group-info-value"),
    (".basic-information-item", ".basic-information-item__data--label",
     ".basic-information-item__data--value"),
    (".job-detail__company--information-item", ".company-title", ".company-value"),
    (".box-job-information-general-info-list__item--content",
     ".box-job-information-general-info-list__item--content-title",
     ".box-job-information-general-info-list__item--content-desc, "
     ".box-job-information-general-info-list__item--content-value"),
]


class TopCVSource(BaseSource):
    name = "TopCV"

    # ---------- Trang danh sách ----------

    def build_listing_url(self, keyword: str, page: int, extra_params: dict) -> tuple[str, dict]:
        return f"{BASE_URL}/tim-viec-lam-{keyword}", {**extra_params, "page": page}

    def parse_listing(self, html: str, retrieved_at: datetime) -> list[ListingItem]:
        soup = make_soup(html)
        items: dict[str, ListingItem] = {}
        for card in soup.select("div.job-item-search-result, div.job-item-2, div.job-item"):
            link = card.select_one("h3.title a[href], .title a[href]") or card.find("a", href=_JOB_PATH_RE)
            if not link:
                continue
            url, job_id = self._canonicalize(link["href"])
            if not job_id or job_id in items:
                continue
            items[job_id] = ListingItem(
                job_id=job_id,
                url=url,
                title=clean_text(link) or link.get("title"),
                company=clean_text(card.select_one("a.company, .company-name, .company")),
                date_hint=parse_relative_date(card.get_text(" ", strip=True), retrieved_at),
            )
        if not items:
            # Dự phòng khi layout thẻ tin thay đổi: quét mọi liên kết tới tin tuyển dụng.
            for link in soup.find_all("a", href=_JOB_PATH_RE):
                url, job_id = self._canonicalize(link["href"])
                if job_id and job_id not in items:
                    items[job_id] = ListingItem(job_id=job_id, url=url, title=clean_text(link))
        return list(items.values())

    @staticmethod
    def _canonicalize(href: str) -> tuple[str, Optional[str]]:
        parts = urlsplit(urljoin(BASE_URL, href))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        match = _JOB_PATH_RE.search(parts.path)
        if not match:
            return url, None
        return url, f"topcv_{match.group(1) or match.group(2)}"

    # ---------- Trang chi tiết ----------

    def parse_detail(self, html: str, item: ListingItem, retrieved_at: datetime) -> Optional[JobPosting]:
        soup = make_soup(html)
        ld = self._extract_job_posting_ld(soup)
        info = self._extract_labeled_info(soup)
        sections = self._extract_sections(soup)

        canonical = soup.find("link", rel="canonical")
        source_url = item.url
        if canonical and canonical.get("href"):
            canon_url, canon_id = self._canonicalize(canonical["href"])
            if canon_id == item.job_id:
                source_url = canon_url

        return JobPosting(
            job_id=item.job_id,
            source=self.name,
            source_url=source_url,
            archive_source=None,
            posting_date=parse_date(ld.get("datePosted")) or item.date_hint
            or parse_date(_pick(info, "ngày đăng")),
            deadline=parse_date(ld.get("validThrough")) or self._extract_deadline(soup, info),
            retrieval_date=retrieved_at.strftime(DATETIME_FORMAT),
            job_title_raw=_single_line(clean_text(soup.select_one("h1.job-detail__info--title, h1"))
                                       or as_text(ld.get("title")) or item.title),
            company=_single_line(self._extract_company(soup) or as_text(ld.get("hiringOrganization"))
                                 or item.company),
            location=_pick(info, "địa điểm", "khu vực") or _format_ld_location(ld),
            salary=_pick(info, "mức lương", "thu nhập", "lương") or _format_ld_salary(ld.get("baseSalary")),
            experience_required=_pick(info, "kinh nghiệm") or _format_ld_experience(ld.get("experienceRequirements")),
            education_required=_pick(info, "học vấn", "trình độ", "bằng cấp")
            or as_text(ld.get("educationRequirements")),
            employment_type=normalize_employment_type(", ".join(filter(None, [
                as_text(ld.get("employmentType")), _pick(info, "hình thức làm việc", "hình thức"),
            ]))),
            job_description=sections.get("description") or html_to_text(ld.get("description")),
            job_requirements=sections.get("requirements")
            or html_to_text(as_text(ld.get("qualifications")) or as_text(ld.get("skills"))),
            job_benefits=sections.get("benefits") or html_to_text(as_text(ld.get("jobBenefits"))),
            category_raw=self._extract_category_tags(soup) or as_text(ld.get("occupationalCategory"))
            or item.search_category,
            industry_raw=as_text(ld.get("industry")) or _pick(info, "lĩnh vực", "ngành nghề"),
        )

    @staticmethod
    def _extract_job_posting_ld(soup: BeautifulSoup) -> dict:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or script.get_text(), strict=False)
            except (ValueError, TypeError):
                continue
            for obj in _iter_ld_objects(data):
                obj_type = obj.get("@type")
                if obj_type == "JobPosting" or (isinstance(obj_type, list) and "JobPosting" in obj_type):
                    return obj
        return {}

    @staticmethod
    def _extract_labeled_info(soup: BeautifulSoup) -> dict[str, str]:
        info: dict[str, str] = {}
        for container_sel, label_sel, value_sel in _LABELED_BLOCKS:
            for block in soup.select(container_sel):
                label, value = clean_text(block.select_one(label_sel)), clean_text(block.select_one(value_sel))
                if label and value:
                    info.setdefault(label.rstrip(":").strip().lower(), value.replace("\n", " "))
        return info

    @staticmethod
    def _extract_sections(soup: BeautifulSoup) -> dict[str, str]:
        sections: dict[str, str] = {}
        for block in soup.select(".job-description__item"):
            key = _classify_heading(clean_text(block.find(_HEADING_TAGS)))
            content = clean_text(block.select_one(".job-description__item--content"))
            if key and content:
                sections.setdefault(key, content)
        if len(sections) == len(_SECTION_KEYWORDS):
            return sections

        # Dự phòng cho layout khác (vd. trang /brand/): gom nội dung sau mỗi tiêu đề mục.
        for heading in soup.find_all(_HEADING_TAGS):
            # Tiêu đề "Yêu cầu:" trong khối tag kỹ năng không phải mục nội dung.
            if heading.find_parent(class_=re.compile("job-tags")):
                continue
            key = _classify_heading(clean_text(heading))
            if not key or key in sections:
                continue
            parts = []
            for sibling in heading.find_next_siblings():
                if sibling.name in _HEADING_TAGS:
                    break
                if text := clean_text(sibling):
                    parts.append(text)
            if not parts and heading.parent:
                if text := clean_text(heading.parent.find_next_sibling()):
                    parts.append(text)
            if parts:
                sections[key] = "\n".join(parts)
        return sections

    @staticmethod
    def _extract_company(soup: BeautifulSoup) -> Optional[str]:
        node = soup.select_one(
            ".company-name-label a, .job-detail__company--information-item.company-name a, "
            "h2.company-name-label, .company-name a, .company-name"
        )
        return clean_text(node)

    @staticmethod
    def _extract_deadline(soup: BeautifulSoup, info: dict[str, str]) -> Optional[str]:
        if value := parse_date(_pick(info, "hạn nộp")):
            return value
        node = soup.select_one(".job-detail__info--deadline")
        if node and (value := parse_date(clean_text(node))):
            return value
        text_node = soup.find(string=re.compile(r"Hạn nộp", re.IGNORECASE))
        if text_node is None or text_node.parent is None:
            return None
        parent = text_node.parent
        return parse_date(clean_text(parent)) or parse_date(clean_text(parent.parent))

    @staticmethod
    def _extract_category_tags(soup: BeautifulSoup) -> Optional[str]:
        tags = []
        # Ưu tiên "Danh mục nghề liên quan"; tag kỹ năng có lẫn kinh nghiệm/học vấn nên chỉ lấy link nghề.
        nodes = soup.select(".box-category-tag") or soup.select(".job-tags__group-list-tag a.link, .job-detail__tag a")
        for node in nodes:
            text = clean_text(node)
            if text and text not in tags:
                tags.append(text)
        return " | ".join(tags) or None


def _single_line(text: Optional[str]) -> Optional[str]:
    return re.sub(r"\s+", " ", text).strip() if text else None


def _iter_ld_objects(data: Any) -> Iterator[dict]:
    if isinstance(data, list):
        for entry in data:
            yield from _iter_ld_objects(entry)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _iter_ld_objects(data["@graph"])


def _classify_heading(text: Optional[str]) -> Optional[str]:
    if not text or len(text) > 60:
        return None
    lowered = text.lower()
    for key, keywords in _SECTION_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return key
    return None


def _pick(info: dict[str, str], *keywords: str) -> Optional[str]:
    for keyword in keywords:
        for label, value in info.items():
            if keyword in label:
                return value
    return None


def _format_ld_location(ld: dict) -> Optional[str]:
    if "TELECOMMUTE" in str(ld.get("jobLocationType", "")).upper():
        return "Remote"
    locations = ld.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    places = []
    for loc in locations:
        address = loc.get("address", {}) if isinstance(loc, dict) else {}
        if isinstance(address, dict):
            parts = [address.get(k) for k in ("addressLocality", "addressRegion")]
            place = ", ".join(dict.fromkeys(p.strip() for p in parts if p))
        else:
            place = as_text(address)
        if place and place not in places:
            places.append(place)
    return "; ".join(places) or None


def _format_ld_salary(base: Any) -> Optional[str]:
    if not isinstance(base, dict):
        return as_text(base)
    value = base.get("value")
    if not isinstance(value, dict):
        return as_text(value)
    low, high = value.get("minValue"), value.get("maxValue")
    currency = base.get("currency") or ""

    def fmt(amount: Any) -> Optional[str]:
        try:
            number = float(amount)
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        if currency == "VND" and number >= 1_000_000:
            return f"{number / 1_000_000:g}"
        return f"{number:g}"

    low_s, high_s = fmt(low), fmt(high)
    unit = " triệu" if currency == "VND" and (low_s or high_s) else f" {currency}".rstrip()
    if low_s and high_s:
        return f"{low_s} - {high_s}{unit}"
    if low_s:
        return f"Từ {low_s}{unit}"
    if high_s:
        return f"Tới {high_s}{unit}"
    return "Thỏa thuận"


def _format_ld_experience(value: Any) -> Optional[str]:
    if isinstance(value, dict) and value.get("monthsOfExperience") is not None:
        try:
            months = float(value["monthsOfExperience"])
        except (TypeError, ValueError):
            return as_text(value)
        return "Không yêu cầu kinh nghiệm" if months == 0 else f"{months / 12:g} năm"
    return as_text(value)
