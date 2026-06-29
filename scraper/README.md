# Load Shedding Scheduler — Phase 1: Data Source Discovery

This is the data-engineering foundation for the load shedding scheduler
app. No mobile app code lives here — this is purely "can we reliably get
schedule data out of public sources," per the project brief.

## TL;DR

**Both sources are now confirmed working end-to-end, on real data, not
just in a sandbox — across multiple DISCOs, not just one lucky example:**

- **K-Electric**: you ran it live — **621 real feeders** parsed
  successfully, after one bug fix (the PDF-link-finding logic).
- **PITC CCMS** (the shared backend behind LESCO/FESCO/MEPCO/PESCO and
  more): turned out to be **entirely AJAX-free**, contrary to the
  original Phase 1 assessment. Both steps — searching for a feeder, and
  getting its actual schedule — are plain server-rendered HTML, found
  by you via HAR captures and now fully implemented and unit-tested
  against real data. After chasing down a session-handling bug, a
  data-quality quirk, and what turned out to be transient server
  flakiness under heavy testing (not a code bug at all), it now
  succeeds cleanly for **GEPCO (Lalamusa) and Lahore-area feeders alike,
  full `OK` status, unchanged code, on repeat runs**. 43 tests passing.

**The one finding that still matters most for your architecture:** 8+ of
the DISCOs you listed (LESCO, FESCO, MEPCO, PESCO, IESCO, HESCO, QESCO,
plus GEPCO which wasn't on your original list) all run through the same
`ccms.pitc.com.pk` backend. K-Electric is the only genuinely independent
source. If PITC's backend goes down, every one of those DISCOs goes down
with it — see section 2.

---

## Changelog

- **Fixed:** `KElectricProvider`'s PDF-link discovery matched a stale
  URL pattern from a dev mirror found during research instead of the
  live WordPress-based site. Confirmed working: 621 feeders, real run.
- **Corrected, twice:** the original assumption that PITC's feeder
  search needed AJAX/JS-endpoint discovery was wrong (confirmed via your
  first HAR capture — it's a plain GET). The follow-up assumption that
  the *schedule detail* page would need the same treatment was also
  wrong (confirmed via your second HAR capture + saved page source —
  also plain GET, also server-rendered). Both steps are now real,
  working code.
- **A bug I caught before shipping it, not after:** my first draft of
  the detail-page parser built each day's 24-point schedule by
  appending AM and PM entries inside the same loop, which interleaves
  them (00:15, 12:15, 01:15, 13:15, ...) instead of true chronological
  order. That's invisible until two OFF hours land next to each other
  in that interleaved order without being chronologically adjacent —
  e.g. if 00:15 and 12:15 were both OFF, the cycle-merge step would
  have silently fused them into one wrong 13-hour cycle instead of two
  separate 1-hour ones. Caught it with a synthetic test before it ever
  touched your data, fixed the ordering, and added a regression test
  specifically for it (`test_regression_does_not_fuse_non_adjacent_hours`).
  Flagging this not to bury it, but because "the bug didn't trigger on
  the one example I had" is a real trap, and the fix is exactly why
  there's now a synthetic edge-case test alongside the real-data one.
- **Fixed, found on your first real PITC run:** search worked (11
  feeders found across two queries) but every single detail-page fetch
  failed to parse. Root cause: the provider made two fully independent
  `requests.get()` calls — one for search, one per detail page — with
  no shared cookies between them. A real browser carries whatever
  session the search page establishes into the next click
  automatically; my fixture-based tests never caught this because the
  fixture itself was captured BY a real browser, so cookies were already
  in place by the time I saved it. Fixed by using one shared
  `requests.Session()` for the whole `fetch()` call (search + every
  detail request), plus a `Referer` header on detail requests pointing
  at the search results page that linked to them. Locked in with a test
  that asserts exactly one session is created and that it's the one
  making every request (`test_uses_exactly_one_shared_session_for_search_and_all_details`)
  — the kind of test that would have caught this before it ever reached
  a real run. Also added a diagnostic improvement either way: a parse
  failure now logs a real snippet of what the server actually returned,
  so if something still doesn't match, the next run's log is enough to
  diagnose without another round-trip.
- **Resolved:** the blank feeder codes from the previous entry were
  never a bug. A real screenshot of a live search confirmed it directly:
  some rows in PITC's own results have a real detail-page button but
  completely empty Feeder Code and Feeder Name — genuine empty
  placeholder entries in their database, same visual styling as real
  rows, nothing wrong with the extraction. Fixed by skipping rows with
  no feeder code before ever attempting a detail fetch — there's nothing
  meaningful to look up.
- **The real mystery, and how it actually resolved:** after the session
  fix above, search kept succeeding but EVERY detail page for
  Lahore/Islamabad feeders — including ones with perfectly real codes
  like `061720` and `105002` — came back with a schedule table that had
  its header rows but zero data rows. Meanwhile Lalamusa (GEPCO) kept
  working every time. A screenshot proved a same-shaped Lahore feeder
  (`133301`, real code, real name) genuinely had real schedule data when
  clicked directly in a browser — so this wasn't a permanent per-DISCO
  data gap. Mid-investigation, one run hit a `RemoteDisconnected` error
  on the search request itself — the server killing the connection
  outright, the signature of a server pushing back after a sustained
  burst of automated requests (we'd made a lot of them, debugging this
  same issue repeatedly in a short window). Added retry/backoff scoped
  to connection-level failures only (never silently retrying real HTTP
  error codes) and slowed the default pacing down. After a short pause,
  `133301` — the exact feeder from the screenshot — came back fully `OK`
  through the unchanged code. Confirmed for real on the next runs: a
  fresh `grid=Lalamusa` search (10/10 feeders `OK`) and a fresh
  `grid=Lahore` search (5/5 feeders `OK`), same code, no further
  changes. **Conclusion: the failures were transient server-side
  flakiness under heavy testing load, not a code bug and not a
  permanent data gap.** Nothing left to fix here — but it's a real
  lesson for Phase 2: this source can be intermittently flaky in
  practice, so production use should expect and tolerate that (the
  retry/backoff already added is a start, not the whole answer) rather
  than treating a single failed run as proof something's broken.

---

## 1. Source comparison table

| Source | Backing system | Type | Access method | Needs JS rendering? | Difficulty (1–10) | Reliability (1–10) |
|---|---|---|---|---|---|---|
| **K-Electric** | Independent (privatized, own infra) | Static PDF, weekly | `requests` + `bs4` (find link) + `pdfplumber` (parse) | No | 3 | 8 |
| **LESCO / FESCO / MEPCO / PESCO / GEPCO** (PITC CCMS, shared) | Shared backend | HTML, server-rendered, 2-step | `requests` + `bs4`, plain GET both steps | **No — confirmed, twice** | 4 | 7* |
| IESCO / HESCO / QESCO | PITC CCMS (shared, unconfirmed) | Assumed same as above | same as above | assumed no | 4 | 5* |
| LESCO's own site (`lesco.gov.pk`) | Unclear if independent or a mirror | Possibly a static published list | N/A | N/A | **`robots.txt` disallows automated access** |
| Third-party "bill checker" sites | Unofficial, ad-supported | HTML, scraped/copied | possible but not recommended | varies | 4 | 2 |

\* *Reliability is rated per-DISCO, but remember: it's the same backend.
If `ccms.pitc.com.pk` goes down, every DISCO row drops to 0
simultaneously.*

**GEPCO**, not on your original list, showed up directly in real search
results (Gujranwala Electric Power Company) — confirmed reachable
through the same portal, worth deciding whether to add it to scope.

**What's directly confirmed now, via real runs (not sandbox guesses):**
K-Electric's full pipeline (621 feeders). PITC's feeder search
(`flsfeeder_index`, plain GET, found 10 feeders for `grid=Lalamusa`,
1 for `grid=Islamabad`, 10 for `city=Lahore` in your actual runs). PITC's
schedule detail page (`flsfeeder/<hash>/detail`, plain GET, full 24-point
ON/OFF/Actual grid, parsed and converted to cycles).

**Still not directly confirmed:** that IESCO/HESCO/QESCO route through
this same backend specifically (worth a 5-minute spot check on each).

`lesco.gov.pk`'s `robots.txt` disallows automated access — not
attempted, and you shouldn't either; ethical/ToS issue regardless of how
easy it'd be.

---

## 2. Primary / backup / emergency fallback

- **Primary, all WAPDA DISCOs (LESCO/FESCO/MEPCO/PESCO/GEPCO/IESCO/HESCO/QESCO):**
  `ccms.pitc.com.pk` city/grid/feeder search → per-feeder schedule.
  **Fully working now.**
- **Primary, Karachi:** K-Electric's weekly PDF. **Fully working,
  confirmed on a real run.**
- **Backup, all DISCOs:** official helpline/SMS channels (`118`, SMS to
  `8118`/`8119`) — not scrapable, but worth surfacing in the app UI as a
  manual fallback when automated sources are down, since "PITC's backend
  itself is down" is a real single-point-of-failure mode here, not
  hypothetical.
- **Not recommended as a fallback:** third-party "bill checker" sites —
  unofficial, ad-funded, themselves just mirror the official sources
  with no SLA. If PITC is down, treat that as a real outage to surface
  honestly, not something to paper over with a worse source.

This is a more honest picture than "9+ independent providers." Worth
deciding now whether the app's UX should reflect "K-Electric: live" vs.
"everyone else: shared backend, currently up/down" as a combined status.

---

## 3. Architecture

```
ScheduleProvider (abstract interface, providers/base.py)
    ├── KElectricProvider   (providers/k_electric.py)  — WORKING, confirmed live (621 feeders)
    ├── PitcCcmsProvider    (providers/pitc_ccms.py)    — WORKING, confirmed live (search + schedule, both steps)
    └── [future: any new source slots in here the same way]

ProviderRegistry (core/registry.py)
    — runs every provider, isolates failures, never lets one
      broken source stop the others from running

run.py
    — wires providers into the registry, writes normalized JSON,
      prints a summary, sets exit code based on "did ANY source work"
```

**Why this shape:** every provider takes in a source's specific mess
(PDF bytes, HTML tables, whatever quirks a future source has) and hands
back the exact same shape: a `ProviderResult` containing a list of
`FeederSchedule` objects (`core/models.py`). PITC is the clearest proof
this works — its provider internally does two sequential HTTP calls per
feeder (search, then detail) and merges a 24-point ON/OFF grid into
cycle ranges, none of which the registry, the JSON writer, or a future
mobile-app backend need to know anything about. They just see
normalized `FeederSchedule` objects, identical in shape to what
K-Electric produces from a completely different source format (PDF text
vs. HTML tables).

**Two layers of failure isolation, demonstrated for real this run:**
within `PitcCcmsProvider`, one feeder's detail page failing to parse
doesn't drop the other feeders found in the same search (see
`test_one_bad_detail_page_does_not_drop_the_rest`); one query failing
outright doesn't stop other configured queries from running (see
`test_search_failure_for_one_query_does_not_abort_others`). One level
up, `ProviderRegistry` isolates failures *between* providers the same
way. This is the same pattern applied recursively, not two different
mechanisms.

**C++ analogy**, still the core pattern of the whole system: a
pure-virtual interface (`ScheduleProvider`) plus a vector of
pointers-to-base that a driver loop iterates over — the shape you'd use
for a plugin system or hardware drivers behind a HAL. The caller never
downcasts to a concrete type, only ever calls the interface methods.

---

## 4. What's working

### K-Electric — confirmed live, 621 feeders

`providers/k_electric.py` + `providers/k_electric_parser.py`. The link-
discovery bug (matched a stale URL pattern) is fixed and tested
(`tests/test_k_electric_link_selection.py`); the PDF-text parser is
unit-tested against captured samples
(`tests/test_k_electric_parser.py`).

### PITC CCMS — confirmed live, both steps

`providers/pitc_ccms.py` + `providers/pitc_ccms_parser.py`. The full
real flow:

1. `GET flsfeeder_index?search_type=<city|grid|feeder_name|feeder_code>&search_value=<text>`
   → HTML table of matching feeders (code, name, city, grid, disco),
   each linking to step 2.
2. `GET flsfeeder/<hash>/detail?...` → a 24-point AM/PM grid (Tomorrow
   Schedule, Today Schedule, Today Actual, Yesterday Schedule per hour)
   → merged into `Cycle` ranges the same shape K-Electric produces.

Both steps are unit-tested against real captured fixtures
(`tests/test_pitc_ccms_parser.py`, `tests/test_pitc_ccms_detail_parser.py`),
and the orchestration logic itself (search → loop → fetch detail →
assemble result, including the OK/PARTIAL/FAILED decision and the
per-feeder failure isolation) is tested separately with mocked HTTP
calls fed that same real data (`tests/test_pitc_ccms_provider.py`) —
43 tests total, all passing. Confirmed on real, repeat, unchanged-code
runs across two different DISCOs: `grid=Lalamusa` (GEPCO, 10/10 feeders
`OK`) and `grid=Lahore` (5/5 feeders `OK`) — not just one lucky example.

A few things worth knowing about how this works:

- **The "Today Actual" column** tells you not just the plan but whether
  it was honored — populated for every hour already past today, blank
  (`-`) for hours not yet occurred, specially marked for the current
  hour. Currently not surfaced in the normalized output (`cycles` only
  reflects the official *schedule*, to match K-Electric's shape) — the
  raw actual-vs-scheduled comparison is sitting right there in
  `FeederDetail.today_actual` if you want it for a future feature
  (e.g. "this feeder is usually 20 minutes late getting power back").
- **The date header is formatted YYYY-DD-MM**, not ISO 8601 — confirmed
  ("2026-19-06" on a page loaded 2026-06-19). Kept as a raw string
  (`FeederDetail.date_label`) specifically so nothing downstream
  silently misreads day and month swapped.
- **The cycle-boundary assumption is a real, documented one, not
  verified beyond what the source itself implies**: each schedule row
  is treated as covering exactly 1 hour starting at its own label (true
  for every row spacing observed in real data), and consecutive OFF
  rows merge into one cycle. Good enough to answer "is this feeder
  scheduled OFF right now," not a promise of exact-minute precision
  beyond what PITC's own UI shows. See
  `providers/pitc_ccms_parser._merge_off_runs_to_cycles`.
- **Capped at 20 feeders per search query** (`MAX_FEEDERS_PER_QUERY`),
  with a 1-second delay between detail-page requests
  (`REQUEST_DELAY_SECONDS`) and a couple of automatic retries scoped to
  connection-level failures only (`RETRY_TOTAL`/`RETRY_BACKOFF_FACTOR`)
  — not arbitrary numbers. The delay was bumped up and retries added
  after a real run hit a `RemoteDisconnected` error mid-session, the
  signature of a server pushing back after a burst of automated
  requests. Confirmed this wasn't a permanent block — the same code
  succeeded cleanly minutes later — but it's a real signal this source
  can be intermittently flaky under load, worth designing around in
  Phase 2 rather than dismissing as a one-off.

---

## 5. Setup & running it

```bash
cd loadshedding-scheduler
pip install -r requirements.txt --break-system-packages   # or use a venv

# Run the real providers
python3 run.py --pretty

# Also include offline demos (both sources, proven against real captured
# data, in case live sites change or your network blocks something)
python3 run.py --pretty --demo

# Run all unit tests (no network needed, ~0.5s)
python3 -m unittest discover -v
```

**Your actual real runs, fully resolved** (these are genuine, not
illustrative — two separate confirming runs, same unchanged code):

```
[OK]     k_electric      feeders= 621
[OK]     pitc_ccms       feeders=  10    (grid=Lalamusa, GEPCO)
2/2 sources returned usable data
```

```
[OK]     k_electric      feeders= 621
[OK]     pitc_ccms       feeders=   5    (grid=Lahore)
2/2 sources returned usable data
```

Both sources, multiple DISCOs, fully clean. Getting here took a real
debugging arc (see Changelog above) — a session-handling bug, a
genuine data-quality quirk in PITC's own database, and what turned out
to be transient server-side flakiness under heavy testing, not a
permanent problem.

**This sandbox's run**, for reference on what the failure path looks
like — still blocked by my own environment's network allowlist (package
registries only):

```
[FAILED] k_electric      feeders=   0  could not locate current PDF link: 403 ...
[FAILED] pitc_ccms       feeders=   0  search city=Lahore: 403 ...; search grid=Islamabad: 403 ...
[OK]     k_electric_DEMO_OFFLINE_SAMPLE feeders=  16
[OK]     pitc_ccms_DEMO_OFFLINE_SAMPLE  feeders=   1
2/4 sources returned usable data
```

Those 403s are specific to *this sandbox* (no outbound access to
arbitrary websites from where I work) — not a finding about either real
site.

### Expected JSON output shape

Genuine output from this sandbox's `--demo` run — `pitc_ccms_DEMO_OFFLINE_SAMPLE`
entry, unedited:

```json
{
  "source": "pitc_ccms_DEMO_OFFLINE_SAMPLE",
  "status": "ok",
  "feeder_count": 1,
  "meta": {
    "note": "DEMO ONLY -- parsed from a captured fixture file (one real feeder, 011613 MAIN BAZAR), not a live request. Real provider: providers/pitc_ccms.py"
  },
  "error": null,
  "feeders": [
    {
      "feeder_name": "MAIN BAZAR (LALA MUSA)",
      "grid_or_area": "132 KV Lala Musa",
      "cycles": [
        {"start": "00:15", "end": "01:15"},
        {"start": "18:15", "end": "19:15"},
        {"start": "20:15", "end": "21:15"}
      ],
      "raw_location": "011613 MAIN BAZAR (LALA MUSA)"
    }
  ]
}
```

Note the shape is identical to what `k_electric` produces (same
`feeder_name` / `grid_or_area` / `cycles` / `raw_location` fields) —
that's the normalization working as intended, even though the
underlying sources are a PDF and a two-step HTML scrape respectively.

---

## 6. Known limitations (be aware of these before Phase 2)

- **K-Electric: `feeder_name` and `grid_or_area` aren't reliably split**
  — no delimiter in the PDF between the two. PITC's feeders don't have
  this problem (`grid_or_area` comes from a dedicated column).
- **K-Electric: overnight cycles aren't date-rolled** — `21:35–00:05` is
  preserved as-is, not inferred as crossing midnight. PITC's cycles are
  similarly not date-rolled.
- **PITC: cycle boundaries are a documented assumption, not a
  guarantee** — see section 4. Good for "is it OFF now," not
  minute-exact.
- **PITC: "Today Actual" (compliance data) isn't in the normalized
  output yet** — sitting in `FeederDetail.today_actual` if you want it
  later.
- **PITC: IESCO/HESCO/QESCO coverage is inferred, not confirmed
  per-DISCO.** GEPCO, by contrast, IS confirmed (real search results).
- **PITC: the per-feeder detail-page hash IDs look session-scoped** —
  don't cache and reuse across runs, always re-search first.
- **PITC: capped at 20 feeders per query, with a 1s delay and a couple
  of connection-level retries** (`MAX_FEEDERS_PER_QUERY`,
  `REQUEST_DELAY_SECONDS`, `RETRY_TOTAL`) — better-informed defaults now
  than the original guess, after real evidence (a `RemoteDisconnected`
  mid-session) that this source can push back under heavy testing load.
  Not a guarantee it won't happen again; build retry-awareness into
  whatever runs this on a schedule, not just into this one provider.
- **PITC: `city=` search can match more than the intended city.** A real
  `city=Lahore` search returned a grid called "Chota Lahore" tagged
  DISCO=PESCO — an unrelated place that just shares part of the name,
  not the major city (which is LESCO territory). Looks like a substring
  match, not an exact city match. `grid=` searches with a known grid
  station name have been more precise in practice; `run.py`'s default
  query was switched to `grid=Lahore` for this reason. Worth keeping in
  mind before building a city-based query list — verify what a city
  search actually returns before trusting it's *only* that city.
- **PITC: only fetches page 1 of search results.** A real screenshot
  showed 30 pages of results for a single grid search — anything beyond
  the first page (and beyond `MAX_FEEDERS_PER_QUERY`) currently isn't
  reachable. Pagination isn't implemented.
- **K-Electric: no retry/backoff yet** (PITC has it now, K-Electric
  doesn't). Same `urllib3.util.Retry` approach would carry over directly
  if K-Electric turns out to need it too.

---

## 7. Suggested next steps (Phase 2)

1. Spot-check IESCO/HESCO/QESCO routing through the same backend (5
   minutes each), and decide on GEPCO's place in scope.
2. Decide on a real feeder/city/grid query list — not all of Pakistan at
   once. `MAX_FEEDERS_PER_QUERY` and `REQUEST_DELAY_SECONDS` in
   `pitc_ccms.py` will need revisiting once you know real volumes.
3. Add retry/backoff to K-Electric too (PITC already has it), and add
   basic scheduling (cron, or a loop with `time.sleep`) now that both
   providers have proven reliable over multiple real runs.
4. Consider whether to surface PITC's "Today Actual" compliance data —
   it's already parsed, just not in the normalized `cycles` shape.
5. Only then: start thinking about how this JSON feeds the mobile app.
