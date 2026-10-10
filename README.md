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

## Scoring mechanism（评分机制）

The daily headline is the single most notable candidate after a three-layer process.
The headline is chosen by a three-layer process; the "Other signals" section on each
post page is a separate snapshot and is **not** part of this score.

每日头条经由三层流程选出；帖子页里的「今日其他信号」是独立快照，**不计入**该评分。

### Layer 1 — raw score per event（`candidates()`）

| Source | Raw score |
| --- | --- |
| Earthquake M4.5+ | `magnitude + (6 if tsunami) + (3 if alert)` |
| Conflict zone | by severity: war=12, high=9, medium=6, low=3 (default 4) |
| Conflict live event | flat **11** |
| Port congestion | HIGH=8, SEVERE=10, CRITICAL=11 (default 6) |
| Geomagnetic storm (non-Quiet) | flat **7** |
| Solar flare | by class letter: X=13, M=10, C=5 (default 3) |
| GPS jamming (any report) | flat **9** |

### Layer 2 — recency / variety penalty（`apply_recency`）

- Looks back over the last `RECENCY_WINDOW = 5` posted days (excluding today).
- Penalty = `min(recent_appearances, MAX_PENALTY_DAYS=3) × RECENCY_PENALTY=4`
  → a family seen 1 / 2 / 3 times in the window gets −4 / −8 / −12 (capped at −12).
- `adj = score − penalty`, then re-sorted by `(-adj, -score, -ts)`.

### Layer 3 — final pick（`pick_story`）

1. Take the highest-`adj` candidate.
2. If its family was **not** shown recently → it wins.
3. If it **was** shown recently → switch to the first candidate whose family is
   unused recently **and** `adj ≥ MIN_ALT_SCORE=5`.
4. If no qualifying alternative exists → keep the original top (even if repeated).

Families: `quake` · `conflict` (zone + live event) · `maritime` (port) ·
`space` (storm + flare) · `jamming`.

## Back-fill a missed day

If a day was skipped (network or API down), regenerate it and push with the
resilient helper. `push_brief.py` commits using the correct per-user no-reply
identity (`281093441+Franky100-pig@users.noreply.github.com`, so it attributes
to **Franky100-pig**, not the `web-flow` bot) and pushes via `git`, auto-falling
back to the GitHub REST API when the sandbox proxy blocks git transport:

```bash
python3 generate_brief.py --date YYYY-MM-DD
python3 push_brief.py "daily brief YYYY-MM-DD"
```

## Reading it

Open `index.html` for the archive, or `posts/<date>/index.zh.html` (中文) /
`index.en.html` (English). Numbers are point-in-time snapshots of public keyless feeds
(USGS, NASA FIRMS, OpenSky, national traffic-camera networks) — not an authoritative source.

MIT.
