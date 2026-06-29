"""
providers/pitc_ccms_parser.py

Pure parsing for ccms.pitc.com.pk -- the feeder search-results page AND
the per-feeder detail/schedule page. Same separation-of-concerns
reasoning as k_electric_parser.py: zero network code here, so it's
testable against captured fixtures without hitting the live site.

=== Search results (confirmed from a real HAR capture, 2026-06-19) ===

    GET https://ccms.pitc.com.pk/flsfeeder_index?search_type=grid&search_value=Lalamusa

A PLAIN SERVER-RENDERED PAGE, not an AJAX/JSON endpoint -- the original
Phase 1 assumption (that this needed JS execution to find a hidden API)
was wrong for this page. `search_type` accepts four values (confirmed
from the page's own <select>): "city", "grid", "feeder_name",
"feeder_code". Results render into a single <table id="dynamic-table">
with columns: Feeder Code, Feeder Name, City, Grid Station, Disco. The
Feeder Code cell is a link/button to a per-feeder detail page:

    https://ccms.pitc.com.pk/flsfeeder/<hash>/detail?search_type=...&search_value=...

The <hash> shares a common prefix across every row captured in the same
page load (e.g. "69df52..."), which looks like a timestamp-derived token
generated at render time, not a stable per-feeder database key. Don't
cache these -- always re-search before following a detail link.

=== Detail/schedule page (confirmed from a real HAR + saved-page-source
capture, 2026-06-19) ===

ALSO plain server-rendered HTML, same as the search page -- no AJAX
needed here either. Two tables:

  Table 0 (info): Feeder code+name, Grid, current Feeder Status, Loss %,
  Category, Net Metering Capacity, plus three duration badges with
  helpful `title` attributes: id="total_off" title="Planned",
  id="live_off" title="Actual", id="act_off" title="History".

  Table 1 (schedule grid): two header rows, then 12 data rows. Each row
  covers one AM hour and its PM counterpart 12 hours later (e.g. row 1
  = 00:15 and 12:15), giving 24 sampled half-day points total. Each side
  has 4 columns: Tomorrow-Schedule, Today-Schedule, Today-Actual,
  Yesterday-Schedule. Cell text is reliably just "ON", "OFF", or "-"
  (the styling -- a <span class="label label-danger"> for OFF, a
  <span class="badge badge-primary"> highlighting the CURRENT hour's
  actual reading -- doesn't matter for parsing since .get_text() on the
  cell gives the right string either way; verified this also correctly
  ignores a stray HTML comment, e.g. "ON<!-- ON -->", that shows up
  after some cells in the real markup).

  "-" in the Today-Actual column means that hour hasn't happened yet
  today -- it is NOT the same as "ON" or "OFF" and must not be coerced
  into either.

The page header also displays a date as "Date YYYY-DD-MM" (confirmed:
"2026-19-06" on a page loaded on 2026-06-19, i.e. day and month are
swapped relative to ISO 8601) -- kept as a raw string, not parsed into a
date object, specifically so nothing downstream silently misreads it as
YYYY-MM-DD.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from core.models import Cycle, FeederSchedule

VALID_SEARCH_TYPES = ("city", "grid", "feeder_name", "feeder_code")

_HHMM_RE = re.compile(r"^(\d{2}):(\d{2})$")


# ---------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------


@dataclass
class FeederListing:
    """One row of a search-results page. This is an INTERMEDIATE shape,
    not the final normalized FeederSchedule -- it has no cycle/timing
    data, just enough to identify a feeder and find its detail page."""

    feeder_code: str
    feeder_name: str
    city: str | None
    grid_station: str | None
    disco: str | None
    detail_url: str


def parse_search_results_html(html: str) -> list[FeederListing]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="dynamic-table")
    if table is None:
        return []

    listings: list[FeederListing] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 5:
            continue  # header row (uses <th>) or anything unexpected -- skip

        code_link = cells[0].find("a")
        if code_link is None or not code_link.get("href"):
            continue  # no detail link -- nothing useful to do with this row

        feeder_code = code_link.get_text(strip=True)
        if not feeder_code:
            # Confirmed via a real screenshot of a live search (2026-06-19,
            # city=Lahore): some rows genuinely have an empty Feeder Code
            # AND empty Feeder Name in PITC's own database -- same row
            # styling, same detail-page button, just nothing in it. Not a
            # parsing bug on our end; there's no real feeder identity here
            # to look up a schedule for, so skip it rather than handing
            # the provider a detail_url with nothing meaningful behind it.
            continue

        feeder_name = cells[1].get_text(strip=True)
        city = cells[2].get_text(strip=True) or None
        # "Grid Station" cells contain an embedded newline in the real
        # page (e.g. "132 KV\nLalamusa") -- collapse to single-spaced text.
        grid_station = " ".join(cells[3].get_text(strip=True).split()) or None
        disco = cells[4].get_text(strip=True) or None
        detail_url = code_link["href"]

        listings.append(
            FeederListing(
                feeder_code=feeder_code,
                feeder_name=feeder_name,
                city=city,
                grid_station=grid_station,
                disco=disco,
                detail_url=detail_url,
            )
        )

    return listings


# ---------------------------------------------------------------------
# Detail / schedule page
# ---------------------------------------------------------------------

# Each entry: (time_label "HH:MM", status) where status is "ON", "OFF",
# or None (rendered as "-" -- hasn't happened yet / not applicable).
TimeSeries = list[tuple[str, str | None]]


@dataclass
class FeederDetail:
    feeder_code: str
    feeder_name: str | None  # None on bare-code pages -- see parse_feeder_detail_html
    grid_station: str | None
    feeder_status: str | None  # current real-time status, e.g. "ON"
    category: str | None
    loss_percent: str | None
    date_label: str | None  # raw "YYYY-DD-MM" string, deliberately unparsed
    tomorrow_schedule: TimeSeries = field(default_factory=list)
    today_schedule: TimeSeries = field(default_factory=list)
    today_actual: TimeSeries = field(default_factory=list)
    yesterday_schedule: TimeSeries = field(default_factory=list)


def _cell_status(cell) -> str | None:
    text = cell.get_text(strip=True)
    if text == "-":
        return None
    return text or None  # "ON" / "OFF" pass through as-is


def diagnose_feeder_detail_html(html: str) -> str:
    """Best-effort structural summary of a page that failed to parse as
    a feeder detail page -- for logging only, never raises, and
    deliberately doesn't try to be the parser itself. Pinpoints WHICH
    assumption broke (table count? info-table labels? row cell count?)
    instead of dumping a flat text snippet that's mostly <head>
    boilerplate by the time you're 30KB into a real page."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # noqa: BLE001
        return f"(could not even parse as HTML: {exc})"

    tables = soup.find_all("table")
    parts = [f"{len(tables)} <table> tag(s) found"]

    if len(tables) < 2:
        return "; ".join(parts) + " -- expected at least 2, stopping here"

    info_table, schedule_table = tables[0], tables[1]

    info_cells = info_table.find_all("td")
    labels_found = [
        cell.find("b").get_text(strip=True) for cell in info_cells if cell.find("b")
    ]
    parts.append(f"table[0] (info): {len(info_cells)} <td> cells, <b> labels found={labels_found}")

    # The label being present doesn't guarantee its VALUE is in the
    # shape the parser expects -- e.g. "Feeder:" with no "<code> - <name>"
    # separator would still show up as a perfectly healthy label here
    # while silently leaving feeder_code as None downstream, which is
    # what actually triggers "couldn't find the basics, return None."
    # Showing the real value turns that from a mystery into a one-line
    # answer, without needing a full HTML dump to see it.
    label_values = []
    for cell in info_cells:
        b = cell.find("b")
        if b is None:
            continue
        label = b.get_text(strip=True)
        full_text = cell.get_text(" ", strip=True)
        value = full_text[len(label):].strip()
        label_values.append(f"{label!r}: {value!r}")
    parts.append(f"table[0] label values: {'; '.join(label_values)}")

    schedule_rows = schedule_table.find_all("tr")
    cell_counts = [len(r.find_all("td")) for r in schedule_rows]
    parts.append(f"table[1] (schedule): {len(schedule_rows)} rows, cell-counts={cell_counts[:16]}")

    # also check whether a 3rd+ table exists that might be the REAL
    # schedule table if the page gained an extra table before it
    if len(tables) > 2:
        extra = tables[2]
        extra_rows = extra.find_all("tr")
        parts.append(
            f"table[2] exists too: {len(extra_rows)} rows, "
            f"cell-counts={[len(r.find_all('td')) for r in extra_rows][:16]}"
        )

    return "; ".join(parts)


def parse_feeder_detail_html(html: str) -> FeederDetail | None:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 2:
        return None

    info_table, schedule_table = tables[0], tables[1]

    # --- info table ---
    feeder_code = feeder_name = grid_station = None
    info_cells = info_table.find_all("td")
    for cell in info_cells:
        label_tag = cell.find("b")
        if label_tag is None:
            continue
        label = label_tag.get_text(strip=True)
        full_text = cell.get_text(" ", strip=True)
        value = full_text[len(label):].strip()
        if label == "Feeder:":
            # Usual format: "011613 - MAIN BAZAR (LALA MUSA)". Confirmed
            # live (2026-06-22, feeders 061720/105002/000623/075327, all
            # found via city-name search rather than the grid-name search
            # the original fixture came from): some feeders show ONLY the
            # bare code here, no " - <name>" suffix at all. Treating that
            # bare value as a name (the old behavior) silently left
            # feeder_code as None, which made the whole page look
            # unparseable even though the code -- and a real, if much
            # smaller, schedule grid -- were both genuinely present. A
            # bare value IS the code, never a name with no code attached;
            # feeder_name is left None here and filled in by the caller
            # from the search-results listing instead, since that's the
            # only place a real name exists for these (see
            # feeder_detail_to_schedule's fallback_name parameter).
            if " - " in value:
                feeder_code, feeder_name = value.split(" - ", 1)
                feeder_code, feeder_name = feeder_code.strip(), feeder_name.strip()
            else:
                feeder_code = value
        elif label == "Grid:":
            grid_station = value or None

    feeder_status_el = info_table.find(id="glaxy_status")
    feeder_status = feeder_status_el.get_text(strip=True) if feeder_status_el else None

    category_el = info_table.find(id="category")
    category = category_el.get_text(strip=True) if category_el else None

    loss_el = info_table.find(id="loss_per")
    loss_percent = loss_el.get_text(strip=True) if loss_el else None

    # --- date header: "...Date 2026-19-06" lives outside both tables,
    # search the whole page for it rather than assuming a fixed container.
    date_label = None
    date_match = re.search(r"Date\s+([\d-]+)", html)
    if date_match:
        date_label = date_match.group(1)

    if feeder_code is None:
        # feeder_name is intentionally NOT required here anymore -- see
        # the "Feeder:" handling above. A page with a real code but no
        # attached name is genuinely parseable (and the caller has a
        # fallback for the name); a page with no code at all gives us no
        # stable identity to hang a schedule on, so that's still the one
        # thing worth bailing out on.
        return None  # couldn't find the basics -- page structure likely changed

    # --- schedule grid ---
    # Built as separate AM/PM lists during the row loop, then concatenated
    # (AM first, PM second) so the final series is in true chronological
    # order (00:15 .. 11:15, 12:15 .. 23:15). Appending AM then PM *inside*
    # each row's iteration would interleave them (00:15, 12:15, 01:15,
    # 13:15, ...) -- which looks harmless until two OFF entries land next
    # to each other in that interleaved order without being chronologically
    # adjacent (e.g. 00:15 and 12:15), at which point the cycle-merge step
    # below would wrongly fuse them into one enormous 13-hour cycle. See
    # tests/test_pitc_ccms_parser.py::test_merge_does_not_fuse_non_adjacent_hours.
    am_tomorrow: TimeSeries = []
    am_today: TimeSeries = []
    am_actual: TimeSeries = []
    am_yesterday: TimeSeries = []
    pm_tomorrow: TimeSeries = []
    pm_today: TimeSeries = []
    pm_actual: TimeSeries = []
    pm_yesterday: TimeSeries = []

    rows = schedule_table.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) != 10:
            continue  # header rows have different shapes -- skip

        am_time = cells[0].get_text(strip=True)
        pm_time = cells[5].get_text(strip=True)
        if not _HHMM_RE.match(am_time) or not _HHMM_RE.match(pm_time):
            continue  # not a real data row

        am_tomorrow.append((am_time, _cell_status(cells[1])))
        am_today.append((am_time, _cell_status(cells[2])))
        am_actual.append((am_time, _cell_status(cells[3])))
        am_yesterday.append((am_time, _cell_status(cells[4])))

        pm_tomorrow.append((pm_time, _cell_status(cells[6])))
        pm_today.append((pm_time, _cell_status(cells[7])))
        pm_actual.append((pm_time, _cell_status(cells[8])))
        pm_yesterday.append((pm_time, _cell_status(cells[9])))

    tomorrow_schedule = am_tomorrow + pm_tomorrow
    today_schedule = am_today + pm_today
    today_actual = am_actual + pm_actual
    yesterday_schedule = am_yesterday + pm_yesterday

    return FeederDetail(
        feeder_code=feeder_code,
        feeder_name=feeder_name,
        grid_station=grid_station,
        feeder_status=feeder_status,
        category=category,
        loss_percent=loss_percent,
        date_label=date_label,
        tomorrow_schedule=tomorrow_schedule,
        today_schedule=today_schedule,
        today_actual=today_actual,
        yesterday_schedule=yesterday_schedule,
    )


def _add_one_hour(label: str) -> str:
    match = _HHMM_RE.match(label)
    if not match:
        raise ValueError(f"unexpected time label: {label!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    hour = (hour + 1) % 24
    return f"{hour:02d}:{minute:02d}"


def _merge_off_runs_to_cycles(series: TimeSeries) -> list[Cycle]:
    """Best-effort conversion of a sampled ON/OFF/None series into
    contiguous Cycle(start, end) ranges, the same shape K-Electric's
    schedule produces. ASSUMPTION, not independently confirmed: each row
    represents a block running from its own label to the next row's
    label (i.e. exactly 1 hour, since every row in the real data is
    spaced exactly 1 hour apart) -- good enough to answer "is this
    feeder in a scheduled OFF window right now," not precise enough to
    promise exact-minute boundaries beyond what the source itself gives.
    Only consecutive "OFF" entries merge into one cycle; a "-" (unknown/
    not yet occurred) breaks a run same as "ON" does, on purpose -- it
    must never be silently treated as part of an OFF block."""
    cycles: list[Cycle] = []
    run_start: str | None = None
    run_last: str | None = None

    for label, status in series:
        if status == "OFF":
            if run_start is None:
                run_start = label
            run_last = label
        else:
            if run_start is not None:
                cycles.append(Cycle(start=run_start, end=_add_one_hour(run_last)))
                run_start = None
                run_last = None

    if run_start is not None:
        cycles.append(Cycle(start=run_start, end=_add_one_hour(run_last)))

    return cycles


def feeder_detail_to_schedule(
    detail: FeederDetail,
    *,
    city: str | None = None,
    disco: str | None = None,
    fallback_name: str | None = None,
) -> FeederSchedule:
    """Maps the richer PITC detail data onto the minimal cross-source
    FeederSchedule shape (core/models.py), using TODAY's official
    schedule as `cycles` -- the same thing K-Electric's PDF provides, so
    downstream code doesn't need to know which source a feeder came
    from. The extra PITC-only data (actual compliance, tomorrow/
    yesterday) isn't part of the minimal cross-source contract; callers
    that want it should keep the FeederDetail object around separately
    rather than expecting it on FeederSchedule.

    `city`/`disco`/`fallback_name` are optional keyword-only params, not
    pulled from `detail` itself -- the detail/schedule page doesn't
    reliably carry any of them (city/disco never; feeder_name only
    sometimes, see parse_feeder_detail_html's "Feeder:" handling). The
    caller (PitcCcmsProvider._fetch_one_schedule) already has the
    search-results FeederListing in hand and passes its city/disco/
    feeder_name through here. Name resolution order: the detail page's
    own name (most specific, when present) -> the search listing's name
    (fallback_name) -> the feeder code itself (last resort, so the
    result is never blank). Defaulting all three to None keeps every
    existing call site, including this module's own tests, working
    unchanged."""
    cycles = _merge_off_runs_to_cycles(detail.today_schedule)
    feeder_name = detail.feeder_name or fallback_name or detail.feeder_code
    return FeederSchedule(
        feeder_name=feeder_name,
        grid_or_area=detail.grid_station,
        cycles=cycles,
        raw_location=f"{detail.feeder_code} {feeder_name}".strip(),
        city=city,
        disco=disco,
    )
