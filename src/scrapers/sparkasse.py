"""Sparkassen career pages.

Sparkassen do not share a single ATS; each institute runs its own page or a shared
provider. This adapter reuses the generic HTML strategies with Sparkasse-oriented link
defaults. Keep bank-specific selectors/patterns in banks.yaml options.
"""
from src.scrapers.generic_html import GenericHtmlScraper

SPARKASSE_LINK_PATTERN = r"/(stellenangebot|stellenangebote|stelle|jobs?|karriere/(stellen|jobs))[^?#]*[/_-][^/?#]+"


class SparkasseScraper(GenericHtmlScraper):
    source_type = "sparkasse"

    def fetch_jobs(self, bank):
        bank.options.setdefault("link_pattern", SPARKASSE_LINK_PATTERN)
        return super().fetch_jobs(bank)
