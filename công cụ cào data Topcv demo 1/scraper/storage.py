"""Lưu checkpoint JSONL (hỗ trợ chạy tiếp) và xuất dataset JSON/CSV."""
import json
import logging
from pathlib import Path

import pandas as pd

from scraper.models import JobPosting

logger = logging.getLogger(__name__)


class DatasetStorage:
    def __init__(self, output_dir: str, basename: str = "topcv_jobs"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.basename = basename
        self.checkpoint_path = self.output_dir / f"{basename}.checkpoint.jsonl"
        self.rejected_path = self.output_dir / f"{basename}.rejected.txt"

    def load_checkpoint(self) -> list[tuple[str, dict]]:
        """Đọc các bản ghi đã lưu, trả về danh sách (danh mục tìm kiếm, bản ghi)."""
        if not self.checkpoint_path.exists():
            return []
        entries = []
        with self.checkpoint_path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                try:
                    row = json.loads(line)
                    entries.append((row["search_category"], row["record"]))
                except (ValueError, KeyError):
                    logger.warning("Bỏ qua dòng checkpoint lỗi #%d", line_no)
        return entries

    def load_rejected(self) -> set[str]:
        """job_id đã bị loại ở lần chạy trước (trùng, quá cũ, thiếu dữ liệu)."""
        if not self.rejected_path.exists():
            return set()
        return {line.strip() for line in self.rejected_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    def append_rejected(self, job_id: str) -> None:
        self.rejected_path.parent.mkdir(parents=True, exist_ok=True)
        with self.rejected_path.open("a", encoding="utf-8") as fh:
            fh.write(job_id + "\n")

    def append_checkpoint(self, search_category: str, record: dict) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self.checkpoint_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"search_category": search_category, "record": record}, ensure_ascii=False) + "\n")

    def export(self, records: list[dict]) -> dict[str, Path]:
        df = pd.DataFrame(records, columns=JobPosting.field_names())
        self.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = self.output_dir / f"{self.basename}.json"
        csv_path = self.output_dir / f"{self.basename}.csv"
        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(df.astype(object).where(df.notna(), None).to_dict("records"), fh, ensure_ascii=False, indent=2)
        # utf-8-sig để Excel hiển thị đúng tiếng Việt.
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        return {"json": json_path, "csv": csv_path}
