#!/usr/bin/env python3
"""
osiris_live.py — pull live data from the OSIRIS OSINT platform (osirisai.live).

The dashboard at osirisai.live is itself driven by plain, keyless HTTP endpoints
under /api. This script hits those same endpoints and prints a human-readable
summary, or renders a self-contained HTML snapshot.

Stdlib only. No API key, no dependencies.

Examples
--------
  python3 osiris_live.py stats
  python3 osiris_live.py quakes --min-mag 4 --limit 10
  python3 osiris_live.py cctv --country China --limit 15
  python3 osiris_live.py cctv --near 22.54,114.06 --radius 150
  python3 osiris_live.py flights --near 22.54,114.06 --radius 200 --kind military
  python3 osiris_live.py ports
  python3 osiris_live.py news
  python3 osiris_live.py conflicts
  python3 osiris_live.py space
  python3 osiris_live.py dossier --lat 31.2 --lng 121.5
  python3 osiris_live.py snapshot -o snapshot.html
  python3 osiris_live.py wall --country "Hong Kong" --limit 12 -o wall.html
  python3 osiris_live.py shot --near 22.54,114.06 --radius 100 -o frame.jpg
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.osirisai.live"
TIMEOUT = 30
UA = "osiris-live-cli/1.0 (+local research tool)"


# ---------------------------------------------------------------- transport

def get(path: str, **params) -> dict | list:
    """GET /api/<path> and return parsed JSON."""
    url = f"{BASE}/api/{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            from urllib.parse import urlencode
            url += "?" + urlencode(clean)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"[!] {url} -> HTTP {e.code}")
    except Exception as e:  # network, timeout, bad json
        raise SystemExit(f"[!] {url} -> {type(e).__name__}: {e}")


def parse_latlng(text: str) -> tuple[float, float]:
    try:
        lat, lng = text.split(",")
        return float(lat), float(lng)
    except Exception:
        raise SystemExit("[!] --near expects 'lat,lng', e.g. 22.54,114.06")


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def ts_to_str(ms_or_iso, ms: bool = False) -> str:
    try:
        if ms:
            return datetime.fromtimestamp(ms_or_iso / 1000, tz=timezone.utc).strftime("%m-%d %H:%M UTC")
        return datetime.fromisoformat(str(ms_or_iso).replace("Z", "+00:00")).strftime("%m-%d %H:%M UTC")
    except Exception:
        return str(ms_or_iso)


def ago(ms: int) -> str:
    mins = (datetime.now(tz=timezone.utc).timestamp() * 1000 - ms) / 60000
    if mins < 60:
        return f"{mins:.0f}m ago"
    if mins < 1440:
        return f"{mins / 60:.1f}h ago"
    return f"{mins / 1440:.1f}d ago"


# ---------------------------------------------------------------- commands

def cmd_stats(a) -> None:
    d = get("stats")["stats"]
    print(f"OSIRIS live counters @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for k in ("flights", "sats", "cctv", "weather", "nuclear", "incidents"):
        print(f"  {k:<10} {d.get(k, '-'):>8,}")


def cmd_quakes(a) -> None:
    d = get("earthquakes")
    quakes = [q for q in d["earthquakes"] if (q.get("magnitude") or 0) >= a.min_mag]
    quakes.sort(key=lambda q: q.get("time") or 0, reverse=True)
    print(f"USGS quakes >= M{a.min_mag}  ({len(quakes)} of {d['total']} tracked, newest first)")
    for q in quakes[: a.limit]:
        alert = f" [{q['alert'].upper()}]" if q.get("alert") else ""
        tsu = " TSUNAMI" if q.get("tsunami") else ""
        print(f"  M{q.get('magnitude', 0):<4} {q.get('place', '?')[:52]:<52} "
              f"{q.get('depth', 0):>6.0f}km  {ago(q['time'])}{alert}{tsu}")


def _cam_kind(c: dict) -> str:
    """How this camera can actually be watched."""
    if c.get("stream_url"):
        return (c.get("stream_type") or "stream")[:7]
    if c.get("feed_url"):
        return "snapshot"
    return "-"


def _cam_line(c: dict, dist: float | None = None) -> str:
    loc = ", ".join(x for x in (c.get("city"), c.get("country")) if x) or "?"
    d = f"{dist:>6.0f}km " if dist is not None else ""
    return (f"  {d}{loc[:30]:<30} {(c.get('source') or '?')[:20]:<20} "
            f"{_cam_kind(c):<9} {c.get('name', '')[:36]}")


def cmd_cctv(a) -> None:
    d = get("cctv")
    cams = d["cameras"]
    if a.country:
        needle = a.country.lower()
        cams = [c for c in cams
                if needle in (c.get("country") or "").lower() or needle in (c.get("city") or "").lower()]
    if a.source:
        needle = a.source.lower()
        cams = [c for c in cams if needle in (c.get("source") or "").lower()]
    if a.online_only:
        cams = [c for c in cams if c.get("stream_url")]

    dist = None
    if a.near:
        lat, lng = parse_latlng(a.near)
        scored = []
        for c in cams:
            if c.get("lat") is None or c.get("lng") is None:
                continue
            km = haversine_km(lat, lng, c["lat"], c["lng"])
            if km <= a.radius:
                scored.append((km, c))
        scored.sort(key=lambda t: t[0])
        cams = [c for _, c in scored]
        dist = True

    print(f"CCTV cameras: {len(cams)} matched (platform total {d['total']:,})")
    if dist:
        lat, lng = parse_latlng(a.near)
        print(f"  within {a.radius}km of {lat},{lng}")
    print(f"  columns: km | city, country | source | kind (stream type or 'snapshot') | name")
    for c in cams[: a.limit]:
        print(_cam_line(c))
        if a.urls:
            print(f"          {c.get('stream_url') or c.get('feed_url') or '(no url)'}")
    if len(cams) > a.limit:
        print(f"  ... {len(cams) - a.limit} more (raise --limit)")


def _select_cams(a) -> list[dict]:
    """Shared camera filtering for cctv / wall subcommands."""
    d = get("cctv")
    cams = d["cameras"]
    if a.country:
        needle = a.country.lower()
        cams = [c for c in cams if needle in (c.get("country") or "").lower()
                or needle in (c.get("city") or "").lower()]
    if getattr(a, "source", None):
        needle = a.source.lower()
        cams = [c for c in cams if needle in (c.get("source") or "").lower()]
    if a.near:
        lat, lng = parse_latlng(a.near)
        scored = [(haversine_km(lat, lng, c["lat"], c["lng"]), c) for c in cams
                  if c.get("lat") is not None and c.get("lng") is not None]
        scored = [t for t in scored if t[0] <= a.radius]
        scored.sort(key=lambda t: t[0])
        cams = [c for _, c in scored]
    return cams


def cmd_wall(a) -> None:
    """Render a self-refreshing HTML wall of live cameras."""
    cams = _select_cams(a)
    if not cams:
        raise SystemExit("[!] no cameras matched")

    watchable = [c for c in cams if c.get("stream_url") or c.get("feed_url")]
    streams = [c for c in cams if c.get("stream_url") and c.get("stream_type") == "iframe"]
    images = [c for c in cams
              if c.get("feed_url") or (c.get("stream_type") in ("jpg", "mjpeg") and c.get("stream_url"))]
    videos = [c for c in cams if c.get("stream_type") == "mp4"]

    tiles = []
    for c in streams[: a.limit]:
        tiles.append(f"""<figure><iframe src="{c['stream_url']}" loading="lazy" allowfullscreen></iframe>
<figcaption>{c.get('name')}<small>{c.get('city')}, {c.get('country')} · {c.get('source')} · iframe live</small></figcaption></figure>""")
    for c in videos[: max(0, a.limit - len(tiles)) // 2]:
        tiles.append(f"""<figure><video src="{c['stream_url']}" autoplay muted playsinline></video>
<figcaption>{c.get('name')}<small>{c.get('city')}, {c.get('country')} · {c.get('source')} · mp4</small></figcaption></figure>""")
    for c in images:
        if len(tiles) >= a.limit:
            break
        url = c.get("feed_url") or c.get("stream_url")
        tiles.append(f"""<figure><img class="live" data-src="{url}" alt="{c.get('name')}">
<figcaption>{c.get('name')}<small>{c.get('city')}, {c.get('country')} · {c.get('source')} · snapshot</small></figcaption></figure>""")

    scope = (f"within {a.radius:.0f} km of {a.near}" if a.near
             else f"country/city ~ {a.country}" if a.country else "global")
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>OSIRIS live camera wall — {scope}</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; background:#0b0f14; color:#e6edf3;
        font:13px/1.45 -apple-system,"SF Pro Text",Segoe UI,Roboto,sans-serif; }}
 header {{ padding:18px 24px; border-bottom:1px solid #1e2630;
          display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }}
 h1 {{ margin:0; font-size:17px; }}
 .meta {{ color:#7d8b9a; font-size:12px; }}
 .dot {{ width:7px;height:7px;border-radius:50%;background:#33d17a;display:inline-block;margin-right:6px;
        box-shadow:0 0 7px #33d17a; animation:p 1.6s infinite; }}
 @keyframes p {{ 50% {{ opacity:.25; }} }}
 .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:12px; padding:18px 24px 30px; }}
 figure {{ margin:0; background:#121821; border:1px solid #1e2630; border-radius:10px; overflow:hidden; }}
 img,video,iframe {{ width:100%; aspect-ratio:16/9; border:0; display:block; object-fit:cover; background:#0d1218; }}
 figcaption {{ padding:7px 10px; font-size:11.5px; color:#c6d2dd; }}
 figcaption small {{ display:block; color:#6f7d8b; margin-top:2px; }}
 footer {{ padding:0 24px 30px; color:#5b6875; font-size:11.5px; }}
</style></head><body>
<header>
  <h1><span class="dot"></span>OSIRIS live camera wall</h1>
  <span class="meta">{scope} · {len(watchable)} watchable of {len(cams)} matched ·
  snapshots refresh every {a.interval}s</span>
</header>
<div class="grid">
{chr(10).join(tiles)}
</div>
<footer>Snapshot tiles are public JPEG feeds re-fetched with a cache-busting query; coverage depends on the upstream operator staying online.</footer>
<script>
const iv = {int(a.interval * 1000)};
const tick = () => document.querySelectorAll('img.live').forEach(im => {{
  const base = im.dataset.src.split('?')[0];
  im.src = base + '?t=' + Date.now();
}});
tick(); setInterval(tick, iv);
</script>
</body></html>
"""
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"[+] wrote {a.out}  — {len(tiles)} tiles "
          f"({len(streams)} iframe, {len(videos)} mp4, {len(images)} snapshot), "
          f"scope: {scope}")


def cmd_flights(a) -> None:
    d = get("flights")
    kinds = {"commercial": ["commercial_flights"],
             "private": ["private_flights", "private_jets"],
             "military": ["military_flights"],
             "all": ["commercial_flights", "private_flights", "private_jets", "military_flights"]}[a.kind]
    rows = []
    for k in kinds:
        rows += d.get(k, [])

    if a.near:
        lat, lng = parse_latlng(a.near)
        rows = [f for f in rows if f.get("lat") is not None and
                haversine_km(lat, lng, f["lat"], f["lng"]) <= a.radius]
        rows.sort(key=lambda f: haversine_km(lat, lng, f["lat"], f["lng"]))
        print(f"Flights({a.kind}) within {a.radius}km of {lat},{lng}: {len(rows)}")
    else:
        print(f"Flights({a.kind}) currently tracked: {len(rows)} of {d.get('total', 0):,} total")
        rows.sort(key=lambda f: f.get("alt") or 0, reverse=True)

    for f in rows[: a.limit]:
        alt = f"{f.get('alt', 0):>6.0f}m" if f.get("alt") is not None else "     -"
        spd = f"{f.get('speed_knots'):>5.0f}kt" if f.get("speed_knots") else "     -"
        print(f"  {(f.get('callsign') or '?'):<9} {f.get('model') or '?':<12} {alt} {spd} "
              f"{(f.get('registration') or ''):<10} {f.get('lat', 0):>9.4f},{f.get('lng', 0):>9.4f}")


def cmd_ports(a) -> None:
    d = get("maritime")
    print(f"Ports: {d['total_ports']}   Chokepoints: {d['total_chokepoints']}   "
          f"Live AIS ships: {d.get('total_ships', 0)}")
    ports = sorted(d["ports"], key=lambda p: p.get("rank") or 999)
    print("\nTop ports by volume:")
    for p in ports[: a.limit]:
        print(f"  #{p.get('rank', '-'):<3} {p.get('name', '')[:20]:<20} {(p.get('country') or ''):<4} "
              f"{(p.get('type') or ''):<10} {str(p.get('congestion', '')):<8} dwell {p.get('dwell_time', '-')}")
    print("\nChokepoints:")
    for c in d["chokepoints"]:
        extra = c.get("congestion") or c.get("status") or ""
        print(f"  {c.get('name', '')[:30]:<30} {(c.get('country') or ''):<4} {extra}")


def cmd_news(a) -> None:
    d = get("live-news")
    print(f"Live 24/7 news feeds: {d['total']}   categories: {', '.join(d.get('categories', []))}")
    for f in d["feeds"]:
        embed = "embed-ok" if f.get("embed_allowed") else "link-only"
        print(f"  [{f.get('category', '?'):<12}] {f.get('name', '')[:30]:<30} "
              f"{(f.get('city') or '')[:14]:<14} {f.get('language', ''):<4} {embed}")
        if a.urls:
            print(f"          {f.get('url')}")


def cmd_conflicts(a) -> None:
    d = get("conflicts")
    print(f"Conflict zones: {d['totalZones']}   active warzones: {d.get('activeWarzones')}   "
          f"live events: {d.get('totalLiveEvents')}  (refreshes every {d.get('refreshInterval')}s)")
    print("\nZones:")
    for z in d["zones"]:
        print(f"  [{z.get('severity', '?'):<8}] {z.get('label', '')[:44]:<44} {z.get('region', '')}")
    if d.get("liveEvents"):
        print("\nLive events:")
        for e in d["liveEvents"][: a.limit]:
            print(f"  {str(e.get('timestamp') or e.get('time') or '')[:16]:<16} {str(e.get('title') or e.get('label') or e)[:70]}")


def cmd_space(a) -> None:
    d = get("space-weather")
    print(f"Kp index: {d.get('kp_index')}  storm level: {d.get('storm_level')}  "
          f"({d.get('kp_timestamp')})")
    for f in d.get("solar_flares", []):
        print(f"  solar flare {f.get('class')}  peak {f.get('peak')}")
    if d.get("alerts"):
        for al in d["alerts"]:
            print(f"  ALERT {al}")


def cmd_dossier(a) -> None:
    try:
        d = get("region-dossier", lat=a.lat, lng=a.lng)
    except SystemExit as e:
        print(e)
        print("  (region-dossier may require different params; see /docs)")
        return
    print(json.dumps(d, ensure_ascii=False, indent=2)[:4000])


def cmd_shot(a) -> None:
    """Download the current frame of the nearest snapshot camera (proof of liveness)."""
    cams = _select_cams(a)
    cands = [c for c in cams if (c.get("feed_url") or c.get("stream_type") in ("jpg", "mjpeg"))
             and c.get("stream_url") or c.get("feed_url")]
    if not cands:
        raise SystemExit("[!] no snapshot-style camera matched")
    c = cands[0]
    url = c.get("feed_url") or c.get("stream_url")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read()
    with open(a.out, "wb") as fh:
        fh.write(data)
    import hashlib
    print(f"[+] {c.get('name')} ({c.get('city')}, {c.get('country')}) — {c.get('source')}")
    print(f"    {url}")
    print(f"    -> {a.out}  {len(data) / 1024:.1f} KB  sha256={hashlib.sha256(data).hexdigest()[:16]}")


# ---------------------------------------------------------------- snapshot

def build_snapshot(path: str) -> None:
    stats = get("stats")["stats"]
    eq = get("earthquakes")
    ports = get("maritime")
    conf = get("conflicts")
    news = get("live-news")
    cctv = get("cctv")
    space = get("space-weather")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    quakes = sorted(eq["earthquakes"], key=lambda q: q.get("time") or 0, reverse=True)[:12]
    eq_rows = "\n".join(
        f"<tr><td class='m'>M{q.get('magnitude', 0):.1f}</td><td>{q.get('place', '?')}</td>"
        f"<td class='dim'>{q.get('depth', 0):.0f} km</td><td class='dim'>{ago(q['time'])}</td>"
        f"<td><a href=\"{q.get('url', '#')}\" target=\"_blank\">USGS</a></td></tr>"
        for q in quakes)

    top_ports = sorted(ports["ports"], key=lambda p: p.get("rank") or 999)[:12]
    port_rows = "\n".join(
        f"<tr><td class='dim'>#{p.get('rank')}</td><td>{p.get('name')}</td><td class='dim'>{p.get('country')}</td>"
        f"<td class='dim'>{p.get('type')}</td><td>{p.get('congestion')}</td>"
        f"<td class='dim'>{p.get('dwell_time')}</td></tr>" for p in top_ports)

    zone_rows = "\n".join(
        f"<tr><td><span class='sev {z.get('severity')}'>{z.get('severity')}</span></td>"
        f"<td>{z.get('label')}</td><td class='dim'>{(z.get('description') or '')[:110]}</td></tr>"
        for z in conf["zones"])

    news_rows = "\n".join(
        f"<tr><td class='dim'>{f.get('category')}</td><td>{f.get('name')}</td>"
        f"<td class='dim'>{f.get('city')}, {f.get('country')}</td>"
        f"<td><a href=\"{f.get('url')}\" target=\"_blank\">open</a></td></tr>" for f in news["feeds"])

    # a few embeddable YouTube live streams for visual verification
    embeds = [c for c in cctv["cameras"]
              if c.get("stream_type") == "iframe" and "youtube.com/embed" in (c.get("stream_url") or "")][:6]
    embed_html = "\n".join(
        f"<figure><iframe src=\"{c['stream_url']}\" loading=\"lazy\" allowfullscreen></iframe>"
        f"<figcaption>{c.get('name')} — {c.get('city')}, {c.get('country')}</figcaption></figure>"
        for c in embeds) or "<p class='dim'>No embeddable streams returned this run.</p>"

    by_country: dict[str, int] = {}
    for c in cctv["cameras"]:
        k = c.get("country") or "?"
        by_country[k] = by_country.get(k, 0) + 1
    cam_rows = "\n".join(f"<tr><td>{k}</td><td class='dim'>{v:,}</td></tr>"
                         for k, v in sorted(by_country.items(), key=lambda t: -t[1])[:12])

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>OSIRIS live snapshot — {now}</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; background:#0b0f14; color:#e6edf3;
        font:14px/1.5 -apple-system,"SF Pro Text",Segoe UI,Roboto,sans-serif; }}
 header {{ padding:22px 28px; border-bottom:1px solid #1e2630; }}
 h1 {{ margin:0 0 4px; font-size:20px; letter-spacing:.4px; }}
 .sub {{ color:#7d8b9a; font-size:12.5px; }}
 .cards {{ display:flex; flex-wrap:wrap; gap:12px; padding:20px 28px; }}
 .card {{ background:#121821; border:1px solid #1e2630; border-radius:10px;
          padding:14px 18px; min-width:118px; }}
 .card b {{ display:block; font-size:24px; color:#4db8ff; font-variant-numeric:tabular-nums; }}
 .card span {{ color:#7d8b9a; font-size:11.5px; text-transform:uppercase; letter-spacing:.7px; }}
 section {{ padding:8px 28px 26px; }}
 h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:1px; color:#9fb0c0;
       border-left:3px solid #4db8ff; padding-left:9px; margin:22px 0 10px; }}
 table {{ border-collapse:collapse; width:100%; }}
 td, th {{ padding:6px 10px; border-bottom:1px solid #1a222c; text-align:left; vertical-align:top; }}
 th {{ color:#7d8b9a; font-weight:500; font-size:11.5px; text-transform:uppercase; }}
 .dim {{ color:#7d8b9a; }} .m {{ color:#ff6b6b; font-weight:600; }}
 .sev {{ padding:1px 7px; border-radius:9px; font-size:11px; background:#2a2135; color:#c9a0ff; }}
 .sev.war {{ background:#3a1620; color:#ff8080; }}
 .sev.high {{ background:#3a2a12; color:#ffb454; }}
 a {{ color:#4db8ff; text-decoration:none; }} a:hover {{ text-decoration:underline; }}
 .embed {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px; }}
 figure {{ margin:0; background:#121821; border:1px solid #1e2630; border-radius:10px; overflow:hidden; }}
 iframe {{ width:100%; aspect-ratio:16/9; border:0; display:block; }}
 figcaption {{ padding:7px 10px; font-size:11.5px; color:#8b9aa8; }}
 footer {{ padding:16px 28px 34px; color:#5b6875; font-size:11.5px; }}
</style></head><body>
<header>
  <h1>OSIRIS — live intelligence snapshot</h1>
  <div class="sub">captured {now} · source osirisai.live public API · Kp {space.get('kp_index')} ({space.get('storm_level')})</div>
</header>
<div class="cards">
  <div class="card"><b>{stats.get('flights', 0):,}</b><span>flights</span></div>
  <div class="card"><b>{stats.get('sats', 0):,}</b><span>satellites</span></div>
  <div class="card"><b>{stats.get('cctv', 0):,}</b><span>cameras</span></div>
  <div class="card"><b>{stats.get('incidents', 0)}</b><span>incidents</span></div>
  <div class="card"><b>{conf.get('activeWarzones', 0)}</b><span>active warzones</span></div>
  <div class="card"><b>{len(eq['earthquakes'])}</b><span>quakes tracked</span></div>
</div>
<section>
  <h2>Latest earthquakes (M2.5+)</h2>
  <table><tr><th>Mag</th><th>Place</th><th>Depth</th><th>When</th><th></th></tr>{eq_rows}</table>
</section>
<section>
  <h2>Conflict zones</h2>
  <table><tr><th>Severity</th><th>Label</th><th>Description</th></tr>{zone_rows}</table>
</section>
<section>
  <h2>Top container ports &amp; chokepoints</h2>
  <table><tr><th>Rank</th><th>Port</th><th>Country</th><th>Type</th><th>Congestion</th><th>Dwell</th></tr>{port_rows}</table>
</section>
<section>
  <h2>Camera coverage by country</h2>
  <table><tr><th>Country</th><th>Cameras</th></tr>{cam_rows}</table>
</section>
<section>
  <h2>Embeddable live cameras</h2>
  <div class="embed">{embed_html}</div>
</section>
<section>
  <h2>Live 24/7 news feeds</h2>
  <table><tr><th>Category</th><th>Feed</th><th>Location</th><th></th></tr>{news_rows}</table>
</section>
<footer>Generated by osiris_live.py · all figures are a point-in-time snapshot of public keyless feeds (USGS, NASA FIRMS, OpenSky, national road/traffic camera networks).</footer>
</body></html>
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"[+] wrote {path}  ({len(html) / 1024:.0f} KB)")


# ---------------------------------------------------------------- cli

def main() -> None:
    p = argparse.ArgumentParser(description="Live read-only CLI for the OSIRIS OSINT platform.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="global counters")

    q = sub.add_parser("quakes", help="USGS seismic events")
    q.add_argument("--min-mag", type=float, default=2.5)
    q.add_argument("--limit", type=int, default=15)
    q.set_defaults(fn=cmd_quakes)

    c = sub.add_parser("cctv", help="public camera grid")
    c.add_argument("--country")
    c.add_argument("--source")
    c.add_argument("--near", help="lat,lng")
    c.add_argument("--radius", type=float, default=100.0)
    c.add_argument("--limit", type=int, default=20)
    c.add_argument("--urls", action="store_true")
    c.add_argument("--online-only", action="store_true")
    c.set_defaults(fn=cmd_cctv)

    f = sub.add_parser("flights", help="ADS-B aircraft")
    f.add_argument("--kind", choices=["commercial", "private", "military", "all"], default="commercial")
    f.add_argument("--near", help="lat,lng")
    f.add_argument("--radius", type=float, default=200.0)
    f.add_argument("--limit", type=int, default=20)
    f.set_defaults(fn=cmd_flights)

    pt = sub.add_parser("ports", help="ports + chokepoints")
    pt.add_argument("--limit", type=int, default=12)
    pt.set_defaults(fn=cmd_ports)

    n = sub.add_parser("news", help="live TV feeds")
    n.add_argument("--urls", action="store_true")
    n.set_defaults(fn=cmd_news)

    cf = sub.add_parser("conflicts", help="conflict zones + events")
    cf.add_argument("--limit", type=int, default=20)
    cf.set_defaults(fn=cmd_conflicts)

    sp = sub.add_parser("space", help="space weather")
    sp.set_defaults(fn=cmd_space)

    ds = sub.add_parser("dossier", help="region dossier for a point")
    ds.add_argument("--lat", type=float, required=True)
    ds.add_argument("--lng", type=float, required=True)
    ds.set_defaults(fn=cmd_dossier)

    sn = sub.add_parser("snapshot", help="render an HTML snapshot")
    sn.add_argument("-o", "--out", default="snapshot.html")
    sn.set_defaults(fn=None)

    w = sub.add_parser("wall", help="self-refreshing HTML wall of live cameras")
    w.add_argument("--country")
    w.add_argument("--source")
    w.add_argument("--near", help="lat,lng")
    w.add_argument("--radius", type=float, default=100.0)
    w.add_argument("--limit", type=int, default=12)
    w.add_argument("--interval", type=float, default=10.0, help="snapshot refresh seconds")
    w.add_argument("-o", "--out", default="wall.html")
    w.set_defaults(fn=cmd_wall)

    sh = sub.add_parser("shot", help="download the current frame of the nearest camera")
    sh.add_argument("--country")
    sh.add_argument("--source")
    sh.add_argument("--near", help="lat,lng")
    sh.add_argument("--radius", type=float, default=100.0)
    sh.add_argument("-o", "--out", default="frame.jpg")
    sh.set_defaults(fn=cmd_shot)

    a = p.parse_args()
    if a.cmd == "stats":
        cmd_stats(a)
    elif a.cmd == "snapshot":
        build_snapshot(a.out)
    else:
        a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
