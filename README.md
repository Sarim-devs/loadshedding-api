# Pak Roshan — Load Shedding API

A FastAPI backend serving real-time load shedding schedules for 636+ feeders across Pakistan. Built to power the Pak Roshan Flutter app.

Python Scraper → schedule_latest.json → FastAPI → Flutter App

## What it does

WAPDA and K-Electric publish load shedding schedules that are buried and hard to use. This API sits between raw scraped data and the mobile app — serving clean, purpose-built endpoints so the Flutter frontend only downloads what it needs, when it needs it.

- 621 K-Electric feeders (Karachi)
- 15 PITC feeders (Lala Musa, Chota Lahore)
- Handles midnight-crossing outage cycles correctly
- Rate limited (120 req/min per IP)
- In-memory — every response is a dict lookup, no database needed at this scale

## Tech Stack

- Python + FastAPI
- Pydantic for response models
- SlowAPI for rate limiting
- Deployed on Railway

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Service status + data load confirmation |
| `GET /stats` | Per-source status, per-city counts, totals |
| `GET /cities` | Top-level area picker — all cities with feeder counts |
| `GET /grids?city=` | Grids within a city |
| `GET /hierarchy` | Full City → Grid → Feeder tree in one call |
| `GET /feeders` | Flat feeder list with filters |
| `GET /schedule/{feeder_id}` | Full outage cycle list for one feeder |
| `GET /next-outage/{feeder_id}` | Current outage status + next change time |

## Quickstart

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/docs` for the interactive Swagger UI.

Run tests:

```bash
python3 -m unittest discover -v
```
## Project Structure
app/
main.py          # Route handlers
data_store.py    # In-memory data layer
enrichment.py    # City/grid derivation
time_utils.py    # Outage time math
models.py        # Pydantic response models
data/
schedule_latest.json   # Scraper output
tests/
test_time_utils.py
test_enrichment.py
test_api.py

## Part of Pak Roshan
This API is one layer of a larger project:
- **Scraper** — Python scripts pulling live data from LESCO/K-Electric
- **API (this repo)** — FastAPI layer serving clean endpoints
- **Flutter App** — Mobile app with notifications, widgets, offline mode

## Author
Muhammad Sarim Usman — 2nd semester CS student, FAST NUCES Rawalpindi





## Project Structure
