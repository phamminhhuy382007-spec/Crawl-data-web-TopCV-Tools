from scraper.sources.base import BaseSource
from scraper.sources.topcv import TopCVSource

SOURCES: dict[str, type[BaseSource]] = {"topcv": TopCVSource}
