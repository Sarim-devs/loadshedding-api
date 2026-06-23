# Deployment

The backend works perfectly today and nobody but you can reach it —
it's bound to `127.0.0.1`/`10.0.2.2`, which only resolves on your own
dev machine. This is the actual remaining blocker to a real, usable
app. Everything below gets you a public HTTPS URL.

## Why Railway

Free tier covers this comfortably (one small FastAPI process, a
~400KB data file, low traffic). Simplest path for a plain Python repo —
no Dockerfile required, it detects `requirements.txt` and the
`Procfile` automatically. Render is a fine alternative with the same
basic steps if you'd rather use that instead.

## What's already prepared

- **`Procfile`** — tells the platform how to start the app:
  `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`. `$PORT` is
  set by the platform at runtime, not by you — binding to a hardcoded
  port is what would actually break a cloud deploy, since the platform
  picks the port and routes traffic to it.
- **CORS** is already wide open (`allow_origins=["*"]`) — fine as-is,
  this is public read-only schedule data, nothing to protect.
- **The data file** (`data/schedule_latest.json`) ships as part of the
  repo, same as it does locally — no database, no extra setup.

## Steps

1. **Push this project to a GitHub repo** (a new, separate one from
   the scraper — this is the API project, `loadshedding-api`, not
   `loadshedding-scheduler`). If you haven't used git before:
   ```bash
   cd loadshedding-api
   git init
   git add .
   git commit -m "Initial deploy"
   ```
   Then create a repo on github.com and follow its "push an existing
   repository" instructions.

2. **railway.app** → sign in with GitHub → **New Project** → **Deploy
   from GitHub repo** → pick this repo.

3. Railway auto-detects Python, installs `requirements.txt`, and uses
   the `Procfile` to start it. First deploy takes a minute or two.

4. **Settings → Networking → Generate Domain.** This gives you the
   public URL — something like `loadshedding-api-production.up.railway.app`.

5. **Verify it's actually live:**
   ```bash
   curl https://<your-railway-domain>/health
   ```
   Should show the same `data_run_at`/`total_feeders` you see locally.

## Point the Flutter app at it

One line to change, in `lib/services/api_service.dart`:

```dart
static const String baseUrl = 'https://<your-railway-domain>';
```

This replaces the `10.0.2.2`/`127.0.0.1` value that only worked for
local dev — once this points at the deployed URL, the app works from
any device, anywhere, not just your emulator on your machine.

## Updating data after a fresh scrape

There's no live scraping happening on the server — same as today,
that's still a deliberate manual step on your machine. The deploy
workflow is just:

1. Run `python3 run.py --pretty` (and `merge_expanded_feeders.py` if
   you're folding in expansion results) in the scraper project, as
   usual.
2. Copy the fresh `output/schedule_latest.json` into the **API**
   project's `data/schedule_latest.json`.
3. `git add data/schedule_latest.json && git commit -m "Refresh schedule data" && git push`

Railway auto-redeploys on every push to the connected branch — no
dashboard clicking required, just the same `git push` you'd do anyway.

## Honest limitations of this setup, for later

- **No scheduled re-scraping.** Data goes stale the moment you stop
  manually running step 1 above. A GitHub Action on a cron schedule
  that runs `run.py` and commits the result would close this gap —
  worth doing once the deploy itself is proven out, not before.
- **Free tier sleep/limits** — Railway's free tier has a monthly usage
  cap, not a hard "sleeps after inactivity" like some other free
  tiers, but worth checking current limits on their pricing page
  before assuming it's free forever at higher traffic.
