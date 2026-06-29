"""
providers/k_electric.py

K-Electric is Karachi's privatized utility and runs completely outside
the PITC/WAPDA ecosystem (see providers/pitc_ccms.py for that one). It
publishes a full-city feeder schedule as a single downloadable PDF every
week, linked from a stable landing page. No login, no per-customer
reference number, no JavaScript rendering required.

Two-step fetch, by design:
  1. GET the landing page, use BeautifulSoup to find the <a> tag pointing
     at this week's PDF (the filename includes the date and changes every
     week, so we discover it instead of guessing it).
  2. GET that PDF URL directly and parse the bytes.

This keeps the "find the real download link" logic resilient: if KE
reorders the page or changes the filename pattern, we still find the
right file as long as it's still a PDF link containing
"weekly_load_shed" somewhere in its path -- we're not hardcoding a date.

Status of this file: CONFIRMED working link-discovery against the live
site as of 2026-06-19. The original version of this file assumed PDFs
lived under "/download/weekly_load_shed/..." based on a dev/staging
mirror found during Phase 1 research -- that assumption was wrong. KE's
real site is WordPress-based; schedule PDFs live under
"/wp-content/uploads/<year>/<month>/" alongside other unrelated PDFs
(defaulters list, feeder losses) that share the same generic "Download"
link text. _find_current_pdf_url() disambiguates by filename content,
not link text or path -- see the hint lists right above that method.
The PDF-parsing logic itself (k_electric_parser.py) is still unit-tested
against a real captured sample; only the link-discovery step needed
correcting once tested against the live site.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO

import pdfplumber
import requests
from bs4 import BeautifulSoup

from core.models import FetchStatus, ProviderResult
from providers.base import ScheduleProvider
from providers.k_electric_parser import parse_schedule_text

logger = logging.getLogger(__name__)


class KElectricProvider(ScheduleProvider):
    LANDING_PAGE = "https://www.ke.com.pk/load-shed-schedule/"
    REQUEST_HEADERS = {
        # Plain requests' default User-Agent is sometimes blocked outright
        # by sites fronted with bot-protection. A realistic browser UA is
        # the cheapest fix and isn't "spoofing" anything malicious -- we're
        # still identifying as a normal HTTP client making a normal GET.
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    TIMEOUT_SECONDS = 15

    @property
    def name(self) -> str:
        return "k_electric"

    def fetch(self) -> ProviderResult:
        """Top-level orchestration. Every failure mode returns a
        ProviderResult instead of raising -- see providers/base.py."""
        try:
            pdf_url = self._find_current_pdf_url()
        except Exception as exc:  # noqa: BLE001 - intentionally broad, see base.py
            logger.error("k_electric: link discovery failed: %s", exc)
            return ProviderResult(
                source=self.name,
                status=FetchStatus.FAILED,
                error=f"could not locate current PDF link: {exc}",
            )

        try:
            pdf_bytes = self._download(pdf_url)
        except Exception as exc:
            logger.error("k_electric: download failed: %s", exc)
            return ProviderResult(
                source=self.name,
                status=FetchStatus.FAILED,
                error=f"PDF download failed: {exc}",
                meta={"pdf_url": pdf_url},
            )

        try:
            feeders, week_label = self._parse_pdf(pdf_bytes)
        except Exception as exc:
            logger.error("k_electric: PDF parsing failed: %s", exc)
            return ProviderResult(
                source=self.name,
                status=FetchStatus.FAILED,
                error=f"PDF parsing failed: {exc}",
                meta={"pdf_url": pdf_url},
            )

        if not feeders:
            logger.warning("k_electric: PDF parsed but found 0 feeder rows")
            return ProviderResult(
                source=self.name,
                status=FetchStatus.PARTIAL,
                error="PDF downloaded but 0 feeders parsed -- the row format "
                "may have changed, see docs/debugging_guide.md",
                meta={"pdf_url": pdf_url, "week_label": week_label},
            )

        logger.info("k_electric: parsed %d feeders OK", len(feeders))
        return ProviderResult(
            source=self.name,
            status=FetchStatus.OK,
            feeders=feeders,
            meta={"pdf_url": pdf_url, "week_label": week_label},
        )

    # Filename hints, not path hints -- confirmed against the live site
    # (2026-06-19): KE migrated to WordPress at some point after my
    # original research, so the old "/download/weekly_load_shed/..."
    # path is dead. PDFs now live under "/wp-content/uploads/<year>/<month>/"
    # with a generic "Download" link text shared by every PDF on the page
    # (schedule, defaulters list, feeder losses), so the link TEXT can't
    # disambiguate them -- only the filename can.
    _SCHEDULE_FILENAME_HINTS = ("lsm", "load-shed", "loadshed", "load_shed")
    _EXCLUDE_FILENAME_HINTS = ("defaulter", "loss", "tariff", "complaint")

    def _find_current_pdf_url(self) -> str:
        resp = requests.get(
            self.LANDING_PAGE, headers=self.REQUEST_HEADERS, timeout=self.TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        pdf_links = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if href.lower().endswith(".pdf"):
                pdf_links.append(href if href.startswith("http") else f"https://ke.com.pk{href}")

        if not pdf_links:
            raise RuntimeError(f"no .pdf links found at all on {self.LANDING_PAGE}")

        return self._select_schedule_pdf_url(pdf_links)

    @classmethod
    def _select_schedule_pdf_url(cls, candidate_urls: list[str]) -> str:
        """Pure function, no network -- pick the schedule PDF out of a list
        of candidate PDF URLs by filename content. Separated from
        _find_current_pdf_url() specifically so this disambiguation logic
        can be unit-tested directly (see tests/test_k_electric_link_selection.py)
        without needing to mock an HTTP response."""

        def score(url: str) -> int:
            name = url.lower()
            if any(h in name for h in cls._EXCLUDE_FILENAME_HINTS):
                return -1
            if any(h in name for h in cls._SCHEDULE_FILENAME_HINTS):
                return 2
            return 0  # unrecognized filename -- neither clearly the schedule nor clearly excluded

        ranked = sorted(((score(u), u) for u in set(candidate_urls)), reverse=True)
        best_score, best_url = ranked[0]

        if best_score <= 0:
            # Don't silently guess between ambiguous candidates -- a wrong
            # silent pick (e.g. returning the defaulters list as if it were
            # the schedule) is worse than a loud, explicit failure here.
            raise RuntimeError(
                f"found {len(candidate_urls)} PDF link(s) but none clearly "
                f"matched the load-shed schedule by filename: "
                f"{[u for _, u in ranked]} -- check which one is current and "
                "update _SCHEDULE_FILENAME_HINTS in k_electric.py"
            )

        return best_url

    def _download(self, pdf_url: str) -> bytes:
        resp = requests.get(pdf_url, headers=self.REQUEST_HEADERS, timeout=self.TIMEOUT_SECONDS)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower():
            # Common failure mode: the "PDF" link actually returns an HTML
            # error/redirect page (e.g. session expired, 404 page styled
            # as 200). Catch it here with a clear message instead of
            # letting pdfplumber fail later with a cryptic error.
            raise RuntimeError(
                f"expected a PDF but got Content-Type={content_type!r} -- "
                "the URL may be returning an HTML error page"
            )
        return resp.content

    def _parse_pdf(self, pdf_bytes: bytes) -> tuple[list, str | None]:
        week_label = None
        text_parts = []
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                text_parts.append(text)
                if week_label is None:
                    match = re.search(r"For the week of[^\n]*", text)
                    if match:
                        week_label = match.group(0).strip()

        full_text = "\n".join(text_parts)
        feeders = parse_schedule_text(full_text)
        return feeders, week_label
