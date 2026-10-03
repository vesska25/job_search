"""Scraper registry: maps banks.yaml `source_type` to adapter class."""
from src.scrapers.base import BaseScraper, ScraperError
from src.scrapers.custom_api import CustomApiScraper
from src.scrapers.generic_html import GenericHtmlScraper
from src.scrapers.personio import PersonioScraper
from src.scrapers.smartrecruiters import SmartRecruitersScraper
from src.scrapers.softgarden import SoftgardenScraper
from src.scrapers.sparkasse import SparkasseScraper
from src.scrapers.successfactors import SuccessFactorsScraper
from src.scrapers.workday import WorkdayScraper

SCRAPERS = {
    "personio": PersonioScraper,
    "workday": WorkdayScraper,
    "successfactors": SuccessFactorsScraper,
    "smartrecruiters": SmartRecruitersScraper,
    "softgarden": SoftgardenScraper,
    "custom_api": CustomApiScraper,
    "custom_html": GenericHtmlScraper,
    "sparkasse": SparkasseScraper,
}
# 'proprietary' is a bank-specific site: use custom_html/custom_api options.
SCRAPERS["proprietary"] = GenericHtmlScraper


def get_scraper(source_type: str, http, **kw) -> BaseScraper:
    try:
        return SCRAPERS[source_type](http, **kw)
    except KeyError:
        raise ScraperError(f"No scraper for source_type '{source_type}'") from None
