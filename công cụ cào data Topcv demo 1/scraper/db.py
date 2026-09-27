"""Lưu bản ghi vào PostgreSQL bằng upsert theo job_id."""
import logging
from typing import Iterable

import psycopg
from psycopg import sql

from scraper.models import JobPosting

logger = logging.getLogger(__name__)

_COLUMN_TYPES = {
    "job_id": "TEXT PRIMARY KEY",
    "source": "TEXT NOT NULL",
    "source_url": "TEXT NOT NULL",
    "posting_date": "DATE",
    "deadline": "DATE",
    "retrieval_date": "TIMESTAMP",
}
_CASTS = {"posting_date": "date", "deadline": "date", "retrieval_date": "timestamp"}


class PostgresStorage:
    def __init__(self, dsn: str, table: str = "job_postings"):
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._table_name = table
        self._table = sql.Identifier(table)
        self._columns = JobPosting.field_names()
        self._ensure_table()
        self._upsert_sql = self._build_upsert()
        logger.info("Đã kết nối PostgreSQL, bảng '%s'.", table)

    def __enter__(self) -> "PostgresStorage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _ensure_table(self) -> None:
        column_defs = sql.SQL(", ").join(
            sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(_COLUMN_TYPES.get(c, "TEXT")))
            for c in self._columns
        )
        with self._conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(self._table, column_defs))
            for column in ("posting_date", "source"):
                index = sql.Identifier(f"{self._table_name}_{column}_idx")
                cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} ({})").format(
                    index, self._table, sql.Identifier(column)))

    def _build_upsert(self) -> sql.Composed:
        placeholders = sql.SQL(", ").join(
            sql.SQL(f"%({c})s::{_CASTS[c]}") if c in _CASTS else sql.Placeholder(c) for c in self._columns
        )
        updates = sql.SQL(", ").join(
            sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in self._columns if c != "job_id"
        )
        return sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (job_id) DO UPDATE SET {}").format(
            self._table, sql.SQL(", ").join(map(sql.Identifier, self._columns)), placeholders, updates
        )

    def upsert(self, record: dict) -> bool:
        try:
            self._conn.execute(self._upsert_sql, {c: record.get(c) for c in self._columns})
            return True
        except psycopg.Error as exc:
            logger.error("Lỗi ghi PostgreSQL %s: %s", record.get("job_id"), exc)
            return False

    def upsert_many(self, records: Iterable[dict]) -> int:
        rows = [{c: r.get(c) for c in self._columns} for r in records]
        if not rows:
            return 0
        try:
            with self._conn.cursor() as cur:
                cur.executemany(self._upsert_sql, rows)
        except psycopg.Error as exc:
            logger.error("Lỗi đồng bộ checkpoint vào PostgreSQL: %s", exc)
            return 0
        return len(rows)

    def close(self) -> None:
        self._conn.close()
