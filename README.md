# OSIRIS Daily Brief

每日从 [osirisai.live](https://www.osirisai.live) 公开 API 自动生成的全球态势简报，中英双语。

A daily, bilingual (zh/en) global-situational brief auto-generated from the public
[osirisai.live](https://www.osirisai.live) API — earthquakes, conflicts, port congestion,
space weather, GPS jamming and live flight counters.

## How it works

- `osiris_live.py` — minimal stdlib fetcher for the OSIRIS `/api/*` endpoints.
- `generate_brief.py` — pulls several feeds, scores every candidate "event", keeps the
  single most notable one, and writes `posts/<YYYY-MM-DD>/index.zh.html` + `index.en.html`,
  then rebuilds the master `index.html` archive.
- A scheduler runs `generate_brief.py` once a day (23:00 Asia/Shanghai) and pushes.

Run manually:

```bash
python3 generate_brief.py                 # today
python3 generate_brief.py --date 2026-10-07   # backfill a day
```

## Reading it

Open `index.html` for the archive, or `posts/<date>/index.zh.html` (中文) /
`index.en.html` (English). Numbers are point-in-time snapshots of public keyless feeds
(USGS, NASA FIRMS, OpenSky, national traffic-camera networks) — not an authoritative source.

MIT.
