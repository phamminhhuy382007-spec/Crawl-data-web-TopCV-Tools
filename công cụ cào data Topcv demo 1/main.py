"""Điểm khởi chạy CLI cho công cụ cào dữ liệu tuyển dụng."""
import argparse
import asyncio
import logging
import os
import sys

from config import TARGET_CATEGORIES, ScraperSettings
from scraper.db import PostgresStorage
from scraper.pipeline import ScrapePipeline
from scraper.sources import SOURCES
from scraper.storage import DatasetStorage
from scraper.utils import slugify


def parse_args() -> argparse.Namespace:
    defaults = ScraperSettings()
    parser = argparse.ArgumentParser(description="Cào dữ liệu tin tuyển dụng (TopCV).")
    parser.add_argument("--source", choices=sorted(SOURCES), default="topcv")
    parser.add_argument("--target-min", type=int, default=defaults.target_min)
    parser.add_argument("--target-max", type=int, default=defaults.target_max)
    parser.add_argument("--lookback-days", type=int, default=defaults.lookback_days)
    parser.add_argument("--concurrency", type=int, default=defaults.concurrency)
    parser.add_argument("--max-pages", type=int, default=defaults.max_pages_per_keyword)
    parser.add_argument("--output", default=defaults.output_dir)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--categories", nargs="*", help="Chỉ chạy các danh mục chỉ định, vd: Marketing QA")
    scope.add_argument(
        "--keywords", nargs="+",
        help='Cào theo tên công việc tùy ý, vd: "kế toán tổng hợp" "java developer"',
    )
    parser.add_argument("--db-table", default="job_postings", help="Tên bảng PostgreSQL")
    parser.add_argument("--no-db", action="store_true", help="Bỏ qua PostgreSQL dù có DATABASE_URL")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    # Console Windows mặc định cp1252 không in được tiếng Việt.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = ScraperSettings(
        target_min=args.target_min, target_max=args.target_max, lookback_days=args.lookback_days,
        concurrency=args.concurrency, max_pages_per_keyword=args.max_pages, output_dir=args.output,
    )
    categories = TARGET_CATEGORIES
    if args.keywords:
        categories = {"Custom": {kw.strip(): [slugify(kw)] for kw in args.keywords if slugify(kw)}}
        if not categories["Custom"]:
            logging.error("Từ khóa không hợp lệ.")
            return 1
    elif args.categories:
        wanted = set(args.categories)
        categories = {g: {c: k for c, k in cats.items() if c in wanted} for g, cats in TARGET_CATEGORIES.items()}
        categories = {g: cats for g, cats in categories.items() if cats}
        if not categories:
            logging.error("Không có danh mục hợp lệ trong: %s", ", ".join(sorted(wanted)))
            return 1

    source = SOURCES[args.source]()
    storage = DatasetStorage(settings.output_dir, basename=f"{args.source}_jobs")
    dsn = os.environ.get("DATABASE_URL")
    if args.no_db or not dsn:
        logging.info("Không dùng PostgreSQL (chưa đặt DATABASE_URL hoặc có --no-db).")
        pipeline = ScrapePipeline(source, settings, categories, storage)
        records = asyncio.run(pipeline.run())
        return 0 if records else 1

    try:
        db = PostgresStorage(dsn, table=args.db_table)
    except Exception as exc:
        logging.error("Không kết nối được PostgreSQL: %s", exc)
        return 1
    with db:
        pipeline = ScrapePipeline(source, settings, categories, storage, db=db)
        records = asyncio.run(pipeline.run())
    return 0 if records else 1


if __name__ == "__main__":
    sys.exit(main())
