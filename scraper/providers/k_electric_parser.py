"""
providers/k_electric_parser.py

Pure parsing functions for K-Electric's weekly load-shed PDF. Deliberately
has ZERO network code in this file -- it only ever touches bytes/text
that something else handed it. That separation is what let me validate
this parser against a real captured sample (tests/fixtures/ke_sample_
extracted_text.txt) without needing network access at all, and it's what
lets *you* unit-test it the same way: feed it text, check what comes out,
no live site required.

C++ analogy
-----------
Same reason you'd split "read bytes from a socket" from "decode the
protocol buffer those bytes represent" into two separate functions: the
decoder can be fuzz-tested / unit-tested with canned byte arrays without
ever opening a socket.

The PDF's actual row format (confirmed from a real download), e.g.:

    36 B RMU Korangi East 1005~1205 1405~1605 1805~2005
    3A RMU Landhi - 1335~1435 -

...is: <feeder name + grid, both free text, NO reliable delimiter between
them> <cycle 1> <cycle 2> <cycle 3>, where each cycle is either "HHMM~HHMM"
or a bare "-" meaning "not shed this cycle".

Key design decision: instead of writing separate regexes to recognize and
skip header lines, footer lines, page-break boilerplate, and the
disclaimer paragraph, this parser does the OPPOSITE -- it only asks "do
the last 3 whitespace-separated tokens on this line look like cycle
data?". Every non-data line in the real PDF (titles, disclaimers, "For
the week of...") fails that test and gets skipped for free. This is more
robust than a denylist of boilerplate strings, because it doesn't break
when KE changes the disclaimer wording next quarter.
"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Cycle, FeederSchedule

_CYCLE_TIME_RE = re.compile(r"^\d{4}~\d{4}$")
_CYCLE_EMPTY_RE = re.compile(r"^-$")


def _parse_cycle_token(token: str) -> Optional[Cycle]:
    """Convert one token ('1005~1205' or '-') into a Cycle, or return
    None if the token isn't a valid cycle at all (caller uses this to
    decide whether a whole line is a data row)."""
    if _CYCLE_EMPTY_RE.match(token):
        return Cycle(start=None, end=None)
    if _CYCLE_TIME_RE.match(token):
        start_raw, end_raw = token.split("~")
        start = f"{start_raw[:2]}:{start_raw[2:]}"
        end = f"{end_raw[:2]}:{end_raw[2:]}"
        return Cycle(start=start, end=end)
    return None


def parse_feeder_line(line: str) -> Optional[FeederSchedule]:
    """Try to parse a single line of extracted PDF text as one feeder's
    row. Returns None if the line doesn't match the expected shape
    (i.e. it's a header/footer/disclaimer line, or a blank line)."""
    tokens = line.strip().split()
    if len(tokens) < 4:  # need at least 1 name token + 3 cycle tokens
        return None

    cycle_tokens = tokens[-3:]
    cycles = [_parse_cycle_token(t) for t in cycle_tokens]
    if any(c is None for c in cycles):
        return None  # last 3 tokens don't look like cycle data -> not a row

    location_tokens = tokens[:-3]
    if not location_tokens:
        return None

    raw_location = " ".join(location_tokens)
    return FeederSchedule(
        feeder_name=raw_location,   # see module note: feeder vs grid is
        grid_or_area=None,           # not reliably splittable from text
        cycles=cycles,               # alone -- see README "Known limitations"
        raw_location=raw_location,
    )


def parse_schedule_text(full_text: str) -> list[FeederSchedule]:
    """Parse every line of extracted PDF text, keeping only lines that
    successfully parse as feeder rows. This is the fallback path, used
    when pdfplumber's table-detection (see k_electric.py) doesn't find a
    clean table grid in the PDF."""
    feeders: list[FeederSchedule] = []
    for line in full_text.splitlines():
        row = parse_feeder_line(line)
        if row is not None:
            feeders.append(row)
    return feeders
