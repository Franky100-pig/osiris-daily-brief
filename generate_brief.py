#!/usr/bin/env python3
"""
generate_brief.py — daily bilingual OSINT brief for osirisai.live

What it does
------------
1. Pulls live public feeds from osirisai.live (quakes, conflicts, ports,
   space weather, flights, global counters).
2. Scores every candidate "event" and keeps the single most notable one.
3. Writes two self-contained HTML files (zh + en) into posts/<date>/.
4. Rebuilds the master index.html that lists every posted day.

Pure stdlib. Run once a day (the scheduler does this). It is safe to run
twice the same day — it overwrites that day's post rather than duplicating.

Usage:
  python3 generate_brief.py            # normal daily run
  python3 generate_brief.py --date 2026-10-07   # backfill / test a specific day
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import osiris_live as O  # the fetcher (same folder)

ROOT = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------- data fetch

def safe_get(path, **kw):
    try:
        return O.get(path, **kw)
    except SystemExit as e:
        return {"__error__": str(e)}


def load_all():
    return {
        "stats": safe_get("stats"),
        "quakes": safe_get("earthquakes"),
        "conflicts": safe_get("conflicts"),
        "maritime": safe_get("maritime"),
        "space": safe_get("space-weather"),
        "flights": safe_get("flights"),
        "fires": safe_get("fires"),
    }


# ------------------------------------------------------------- story pick

# Southern Africa bounding boxes (lat_min, lat_max, lng_min, lng_max).
# Order matters: a detection is attributed to the FIRST box that contains it,
# so border fires (e.g. Angola/DRC) are not double-counted in the roll-up.
SOUTHERN_AFRICA = {
    "Angola": (-18.2, -4.4, 11.6, 24.2),
    "Zambia": (-18.1, -8.2, 21.9, 33.8),
    "Zimbabwe": (-22.5, -15.5, 25.1, 33.2),
    "Botswana": (-27.0, -17.7, 19.8, 29.5),
    "Namibia": (-29.1, -16.9, 11.6, 25.4),
    "Mozambique": (-27.0, -10.4, 30.1, 41.6),
    "Malawi": (-17.2, -9.3, 32.6, 36.0),
    "South Africa": (-34.9, -22.0, 16.4, 33.0),
    "Madagascar": (-25.7, -11.8, 43.1, 50.6),
    "DRC (south)": (-13.6, -5.0, 12.0, 31.4),
}
FIRE_SOURCE_URL = "https://firms.modaps.eosdis.nasa.gov/active_fire/"


def fire_candidates(data):
    """NASA FIRMS active-fire detections (OSIRIS /api/fires), aggregated over
    southern Africa. Produces one candidate per affected country plus a regional
    roll-up. The feed mixes current detections with stray historical records, so
    only the two most recent dates present in the payload are counted."""
    f = data.get("fires")
    if not isinstance(f, dict) or not f.get("fires"):
        return []
    fires = f["fires"]
    days = sorted({x.get("date") for x in fires if x.get("date")})
    if not days:
        return []
    window = set(days[-2:])
    recent = [x for x in fires if x.get("date") in window]
    if not recent:
        return []

    def in_box(x, b):
        return b[0] <= x.get("lat", 99) <= b[1] and b[2] <= x.get("lng", 999) <= b[3]

    # unique attribution: each detection belongs to its first containing box
    assigned = {name: [] for name in SOUTHERN_AFRICA}
    for x in recent:
        for name, b in SOUTHERN_AFRICA.items():
            if in_box(x, b):
                assigned[name].append(x)
                break

    per_country = []
    for name, sub in assigned.items():
        if not sub:
            continue
        hc = sum(1 for x in sub if x.get("confidence") == "high")
        frp = sum(x.get("frp") or 0 for x in sub)
        per_country.append({"name": name, "count": len(sub),
                            "high_conf": hc, "frp": round(frp, 1)})
    per_country.sort(key=lambda c: -c["count"])
    if not per_country:
        return []

    total = sum(c["count"] for c in per_country)
    total_hc = sum(c["high_conf"] for c in per_country)
    total_frp = sum(c["frp"] for c in per_country)
    window_str = f"{min(window)} – {max(window)}"
    top_str = ", ".join(f"{c['name']} ({c['count']})" for c in per_country[:4])

    out = [{
        "kind": "fire", "score": 6 + min(6, total / 100), "ts": 0,
        "raw": {"name": "southern Africa wildfires", "region": "southern Africa",
                "count": total, "high_conf": total_hc, "frp": round(total_frp, 1),
                "per_country": per_country[:6], "window": window_str,
                "global_total": f.get("total"), "source": f.get("source", "NASA FIRMS"),
                "sourceUrl": FIRE_SOURCE_URL},
        "headline_zh": f"南部非洲野火活跃：{total} 处火点",
        "headline_en": f"Wildfires across southern Africa: {total} detections",
    }]
    for c in per_country:
        out.append({
            "kind": "fire", "score": 6 + min(4, c["count"] / 60), "ts": 0,
            "raw": {"name": f"{c['name']} wildfires", "region": c["name"],
                    "count": c["count"], "high_conf": c["high_conf"],
                    "frp": c["frp"], "window": window_str,
                    "source": f.get("source", "NASA FIRMS"),
                    "sourceUrl": FIRE_SOURCE_URL},
            "headline_zh": f"野火：{c['name']} {c['count']} 处火点",
            "headline_en": f"Wildfires in {c['name']}: {c['count']} detections",
        })
    return out


def candidates(data):
    """Return a list of candidate stories, each with a numeric score (higher = more notable)."""
    out = []

    # --- earthquakes (M4.5+) ---
    q = data["quakes"]
    if isinstance(q, dict) and "earthquakes" in q:
        for e in q["earthquakes"]:
            m = e.get("magnitude") or 0
            if m >= 4.5:
                score = m + (6 if e.get("tsunami") else 0) + (3 if e.get("alert") else 0)
                out.append({
                    "kind": "quake", "score": score, "ts": e.get("time", 0), "raw": e,
                    "headline_zh": f"M{m:.1f} 级地震：{e.get('place', '未知地区')}",
                    "headline_en": f"M{m:.1f} earthquake — {e.get('place', 'unknown region')}",
                })

    # --- active conflict zones / live events ---
    c = data["conflicts"]
    if isinstance(c, dict):
        for z in c.get("zones", []):
            sev = (z.get("severity") or "").lower()
            w = {"war": 12, "high": 9, "medium": 6, "low": 3}.get(sev, 4)
            out.append({
                "kind": "conflict", "score": w, "ts": 0, "raw": z,
                "headline_zh": f"冲突热点：{z.get('label', '')}",
                "headline_en": f"Conflict hotspot: {z.get('label', '')}",
            })
        for ev in c.get("liveEvents", []):
            out.append({
                "kind": "conflict_event", "score": 11,
                "ts": 0, "raw": ev,
                "headline_zh": f"实时战况：{ev.get('title') or ev.get('label') or 'a live event'}",
                "headline_en": f"Live event: {ev.get('title') or ev.get('label') or 'a live event'}",
            })

    # --- port congestion ---
    p = data["maritime"]
    if isinstance(p, dict):
        for port in p.get("ports", []):
            cong = (port.get("congestion") or "").upper()
            if cong not in ("", "NORMAL"):
                w = {"HIGH": 8, "SEVERE": 10, "CRITICAL": 11}.get(cong, 6)
                out.append({
                    "kind": "port", "score": w, "ts": 0, "raw": port,
                    "headline_zh": f"港口拥堵：{port.get('name')}（{port.get('country')}）—— {cong}",
                    "headline_en": f"Port congestion: {port.get('name')} ({port.get('country')}) — {cong}",
                })

    # --- chokepoint risk (maritime chokepoints from /api/maritime) ---
    if isinstance(p, dict):
        for cp in p.get("chokepoints", []):
            risk = (cp.get("risk") or "").upper()
            if risk in CHOKEPOINT_WEIGHTS:
                w = CHOKEPOINT_WEIGHTS[risk]
                risk_zh = RISK_ZH.get(risk, risk)
                out.append({
                    "kind": "chokepoint", "score": w, "ts": 0, "raw": cp,
                    "headline_zh": f"咽喉要道：{cp.get('name')} —— 风险{risk_zh}",
                    "headline_en": f"Chokepoint alert: {cp.get('name')} — {risk} risk",
                })

    # --- space weather ---
    s = data["space"]
    if isinstance(s, dict):
        if s.get("storm_level") and s.get("storm_level") != "Quiet":
            out.append({
                "kind": "space", "score": 7, "ts": 0, "raw": s,
                "headline_zh": f"地磁活动：{s.get('storm_level')}（Kp {s.get('kp_index')}）",
                "headline_en": f"Geomagnetic activity: {s.get('storm_level')} (Kp {s.get('kp_index')})",
            })
        for fl in s.get("solar_flares", []):
            cls = (fl.get("class") or "A0")
            lead = cls[0]
            w = {"X": 13, "M": 10, "C": 5}.get(lead, 3)
            out.append({
                "kind": "flare", "score": w, "ts": 0, "raw": fl,
                "headline_zh": f"太阳耀斑：{cls} 级",
                "headline_en": f"Solar flare: class {cls}",
            })

    # --- GPS jamming hotspots ---
    f = data["flights"]
    if isinstance(f, dict) and f.get("gps_jamming"):
        out.append({
            "kind": "jamming", "score": 9, "ts": 0, "raw": f,
            "headline_zh": f"GPS 干扰活跃：{len(f['gps_jamming'])} 处上报",
            "headline_en": f"GPS jamming active: {len(f['gps_jamming'])} reports",
        })

    # --- wildfires (NASA FIRMS active-fire detections via /api/fires) ---
    out.extend(fire_candidates(data))

    out.sort(key=lambda x: (-x["score"], -x.get("ts", 0)))
    return out


# ------------------------------------------------------------- variety / recency

RECENCY_WINDOW = 5        # how many previously posted days to look back at
RECENCY_PENALTY = 4       # points subtracted per recent appearance of the same family
MAX_PENALTY_DAYS = 3      # cap so a heavily-repeated family can't drop below everything
MIN_ALT_SCORE = 5         # an alternative family must score at least this to replace a repeated one

# Per-specific-story recency: a headline that was literally shown recently (e.g. the
# standing "UKRAINE WAR" zone, which always scores 12) must be demoted so the brief does
# not repeat the SAME story day after day. Family penalty alone is too weak for standing
# zones, because they top the raw score and the fallback keeps returning them.
LABEL_RECENCY_PENALTY = 7   # points off if the exact same headline_en appeared recently
MAX_LABEL_PENALTY_DAYS = 2  # cap the per-headline penalty so it can't drop below everything


# --- chokepoint risk weights + nearby-war context (maritime /api/maritime) ---
CHOKEPOINT_WEIGHTS = {"ELEVATED": 6, "HIGH": 8, "SEVERE": 10, "CRITICAL": 12}
RISK_ZH = {"ELEVATED": "升高", "HIGH": "高", "SEVERE": "严重", "CRITICAL": "危急"}
NEARBY_WAR_RADIUS_KM = 800


def _haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km between two lat/lng points."""
    from math import radians, sin, cos, asin, sqrt
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


def family_of(kind):
    """Map a story kind to a coarse topic family used for de-duplication."""
    return {
        "quake": "quake",
        "conflict": "conflict",
        "conflict_event": "conflict",
        "port": "maritime",
        "chokepoint": "maritime",
        "space": "space",
        "flare": "space",
        "jamming": "jamming",
        "fire": "fire",
    }.get(kind, "other")


def family_from_headline_en(en):
    """Best-effort family guess from a stored English headline (older posts)."""
    if en.startswith("Conflict hotspot") or en.startswith("Live event"):
        return "conflict"
    if "earthquake" in en:
        return "quake"
    if en.startswith("Port congestion"):
        return "maritime"
    if en.startswith("Chokepoint alert"):
        return "maritime"
    if en.startswith("Geomagnetic activity") or en.startswith("Solar flare"):
        return "space"
    if en.startswith("GPS jamming"):
        return "jamming"
    if "wildfire" in en.lower():
        return "fire"
    return "other"


_H1_RE = re.compile(r"<h1>(.*?)</h1>", re.S)


def load_recent_families(posts_dir, today, window=RECENCY_WINDOW):
    """Count story families over the most recent `window` posted days (excluding today)."""
    from collections import Counter
    if not os.path.isdir(posts_dir):
        return Counter()
    days = sorted(
        d for d in os.listdir(posts_dir)
        if d != today and os.path.isfile(os.path.join(posts_dir, d, "index.zh.html"))
    )
    recent = days[-window:]
    fam = Counter()
    for d in recent:
        meta = os.path.join(posts_dir, d, "meta.json")
        if os.path.isfile(meta):
            try:
                with open(meta, encoding="utf-8") as fh:
                    fam[json.load(fh).get("family", "other")] += 1
                continue
            except Exception:
                pass
        en = os.path.join(posts_dir, d, "index.en.html")
        if os.path.isfile(en):
            try:
                m = _H1_RE.search(open(en, encoding="utf-8").read())
                if m:
                    fam[family_from_headline_en(m.group(1).strip())] += 1
            except Exception:
                pass
    return fam


def load_recent_headlines(posts_dir, today, window=RECENCY_WINDOW):
    """Count exact English headlines over the most recent `window` posted days
    (excluding today). Used to demote a story that was literally shown recently,
    which the family-level penalty cannot catch for permanent standing zones."""
    from collections import Counter
    if not os.path.isdir(posts_dir):
        return Counter()
    days = sorted(
        d for d in os.listdir(posts_dir)
        if d != today and os.path.isfile(os.path.join(posts_dir, d, "index.zh.html"))
    )
    recent = days[-window:]
    cnt = Counter()
    for d in recent:
        meta = os.path.join(posts_dir, d, "meta.json")
        if os.path.isfile(meta):
            try:
                cnt[json.load(open(meta, encoding="utf-8")).get("headline_en", "")] += 1
                continue
            except Exception:
                pass
        en = os.path.join(posts_dir, d, "index.en.html")
        if os.path.isfile(en):
            try:
                m = _H1_RE.search(open(en, encoding="utf-8").read())
                if m:
                    cnt[m.group(1).strip()] += 1
            except Exception:
                pass
    return cnt


def apply_recency(cands, family_counts, headline_counts=None):
    """Re-sort candidates after penalising recently-shown families (topic variety)
    AND recently-shown specific headlines (so the SAME standing story, e.g. UKRAINE
    WAR, cannot repeat day after day)."""
    headline_counts = headline_counts or {}
    for c in cands:
        fam = family_of(c["kind"])
        c["family"] = fam
        c["penalty"] = min(family_counts.get(fam, 0), MAX_PENALTY_DAYS) * RECENCY_PENALTY
        hc = headline_counts.get(c["headline_en"], 0)
        c["label_penalty"] = min(hc, MAX_LABEL_PENALTY_DAYS) * LABEL_RECENCY_PENALTY
        c["adj"] = c["score"] - c["penalty"] - c["label_penalty"]
    cands.sort(key=lambda x: (-x["adj"], -x["score"], -x.get("ts", 0)))
    return cands


def pick_story(cands, recent_families):
    """Pick the top candidate, but if its family was shown recently, switch to the
    best candidate from a different family so the same topic doesn't repeat daily."""
    if not cands:
        return None
    top = cands[0]
    if recent_families.get(top["family"], 0) == 0:
        return top
    for c in cands:
        if recent_families.get(c["family"], 0) == 0 and c["adj"] >= MIN_ALT_SCORE:
            return c
    return top


def find_urgent(cands, keyword):
    """Find the best candidate mentioning `keyword` (case-insensitive). Prefers
    live conflict events over standing conflict zones. Returns a candidate dict
    or None (caller falls back to the algorithm — never invents data)."""
    kw = (keyword or "").lower().strip()
    if not kw or not cands:
        return None

    def matches(c):
        blob = " ".join(str(c.get(k, "")) for k in ("headline_en", "headline_zh"))
        raw = c.get("raw") or {}
        if isinstance(raw, dict):
            blob += " " + " ".join(str(raw.get(k, "")) for k in
                                   ("title", "label", "name", "description", "summary", "region"))
        return kw in blob.lower()

    hits = [c for c in cands if matches(c)]
    if not hits:
        return None
    # prefer live events over standing zones, then by adjusted score
    hits.sort(key=lambda c: (0 if c["kind"] == "conflict_event" else 1,
                             -c.get("adj", c["score"])))
    return hits[0]


# ------------------------------------------------------------- narration

def narrate(story, data):
    """Return bilingual structured content for the chosen story."""
    raw = story["raw"]
    kind = story["kind"]
    zh, en = [], []
    facts_zh, facts_en = [], []

    if kind == "quake":
        e = raw
        place, m = e.get("place", "未知"), e.get("magnitude", 0)
        when_zh = O.ago(e.get("time", 0), "zh")
        when_en = O.ago(e.get("time", 0), "en")
        zh.append(f"今日最值得记录的地球动态，是一次里氏 {m:.1f} 级地震，发生于{place}，约 {when_zh}。")
        zh.append("地震本身是板块运动的常态，但震级、震源深度与是否触发海啸预警，决定了它是否值得普通人关注。")
        en.append(f"The headline event today is a M{m:.1f} earthquake near {place}, roughly {when_en}.")
        en.append("Quakes are routine tectonics; magnitude, depth and any tsunami flag are what make one worth a mention.")
        facts_zh = [("位置", place), ("震级", f"M{m:.1f}"), ("深度", f"{e.get('depth', 0):.0f} km"),
                    ("发生时间", when_zh), ("海啸", "是" if e.get("tsunami") else "否"),
                    ("USGS 详情", e.get("url", ""))]
        facts_en = [("Location", place), ("Magnitude", f"M{m:.1f}"), ("Depth", f"{e.get('depth', 0):.0f} km"),
                    ("When", when_en), ("Tsunami", "yes" if e.get("tsunami") else "no"),
                    ("USGS", e.get("url", ""))]

    elif kind in ("conflict", "conflict_event"):
        z = raw
        label = z.get("label") or z.get("title") or z.get("name") or "冲突动态"
        desc = z.get("description") or z.get("summary") or ""
        sev = (z.get("severity") or "").lower()
        sev_zh = {"war": "战争级", "high": "高烈度", "medium": "中等", "low": "低烈度"}.get(sev, "活跃")
        zh.append(f"今日的冲突类关注点是「{label}」，严重程度为{sev_zh}。")
        zh.append("这类热点通常直接牵动航运咽喉、能源价格与难民流动，是地缘与市场的早期信号。")
        en.append(f"Today's conflict focus is \"{label}\", rated {sev or 'active'}.")
        en.append("Such hotspots usually move shipping chokepoints, energy prices and migration — early signals for geopolitics and markets.")
        facts_zh = [("区域", z.get("region") or label), ("严重度", z.get("severity") or "-"),
                    ("来源", z.get("sourceUrl") or z.get("source") or "")]
        facts_en = [("Region", z.get("region") or label), ("Severity", z.get("severity") or "-"),
                    ("Source", z.get("sourceUrl") or z.get("source") or "")]
        if desc:
            # The original OSIRIS description is free English text we cannot
            # reliably translate offline, so it is shown only on the EN page to
            # keep the ZH page free of English prose.
            facts_en.append(("Description", desc))
        # Zone headlines (e.g. an NYT story tied to this conflict) are English
        # data from OSIRIS — EN page only, per the language policy. Never
        # invented: we only echo titles/urls exactly as the feed provides them.
        heads = [h for h in (z.get("headlines") or []) if h.get("title")]
        for h in heads[:3]:
            t, u, s = h.get("title", ""), h.get("url", ""), h.get("source", "")
            val = f'<a href="{u}">{t}</a>' if u else t
            if s:
                val += f" — {s}"
            facts_en.append(("In the news", val))
        if heads:
            top = heads[0]
            en.append(f"In the news: \"{top.get('title', '')}\""
                      + (f" ({top.get('source')})" if top.get("source") else "") + ".")

    elif kind == "port":
        port = raw
        zh.append(f"航运方面，{port.get('name')}（{port.get('country')}）当前拥堵等级为 {port.get('congestion')}。")
        zh.append(f"该港类型：{port.get('type')}，集装箱吞吐排名全球第 {port.get('rank')}。平均滞留（dwell）{port.get('dwell_time')}。")
        en.append(f"On shipping, {port.get('name')} ({port.get('country')}) shows {port.get('congestion')} congestion.")
        en.append(f"Port type: {port.get('type')}; ranked #{port.get('rank')} globally by throughput; dwell ~{port.get('dwell_time')}.")
        facts_zh = [("港口", port.get("name")), ("国家", port.get("country")),
                    ("拥堵", port.get("congestion")), ("全球排名", f"#{port.get('rank')}"),
                    ("滞留时间", port.get("dwell_time") or "-")]
        facts_en = [("Port", port.get("name")), ("Country", port.get("country")),
                    ("Congestion", port.get("congestion")), ("Rank", f"#{port.get('rank')}"),
                    ("Dwell", port.get("dwell_time") or "-")]

    elif kind == "chokepoint":
        cp = raw
        name = cp.get("name") or "chokepoint"
        risk = (cp.get("risk") or "-").upper()
        risk_zh = RISK_ZH.get(risk, risk)
        traffic = cp.get("traffic") or "-"
        zh.append(f"航运咽喉「{name}」当前风险等级为「{risk_zh}」，实时流量读数：{traffic}。")
        zh.append("咽喉水道是全球能源与贸易的单点节点，一旦受阻，绕行成本会立刻传导到油运与集运价格上。")
        en.append(f"Maritime chokepoint \"{name}\" is rated {risk} risk; traffic reading: {traffic}.")
        en.append("Chokepoints are single points of failure for energy and trade — disruption feeds "
                  "straight into rerouting costs, tanker rates and container freight.")
        facts_zh = [("要道", name), ("风险等级", risk_zh), ("流量", traffic)]
        facts_en = [("Chokepoint", name), ("Risk", risk), ("Traffic", traffic)]
        # Nearby-war context: conflict zones geographically close to this chokepoint,
        # pulled straight from the conflicts feed — distances computed, nothing invented.
        try:
            lat0, lng0 = float(cp.get("lat")), float(cp.get("lng"))
        except (TypeError, ValueError):
            lat0 = lng0 = None
        if lat0 is not None:
            conf = (data or {}).get("conflicts")
            zones = conf.get("zones", []) if isinstance(conf, dict) else []
            near = []
            for z in zones:
                # "Nearby WAR context": only actual conflict zones (war/high severity),
                # so low-level "elevated" instability zones don't dilute the picture.
                if (z.get("severity") or "").lower() not in ("war", "high"):
                    continue
                try:
                    d_km = _haversine_km(lat0, lng0, float(z.get("lat")), float(z.get("lng")))
                except (TypeError, ValueError):
                    continue
                if d_km <= NEARBY_WAR_RADIUS_KM:
                    near.append((d_km, z))
            near.sort(key=lambda x: x[0])
            if near:
                labels_zh = "、".join(z.get("label") or "?" for _, z in near)
                en_names = [z.get("label") or "?" for _, z in near]
                labels_en = (", ".join(en_names[:-1]) + " and " + en_names[-1]) if len(en_names) > 2 else " and ".join(en_names)
                zh.append(f"把镜头拉远一点：{labels_zh} 就在这条水道旁边——这一线的航运风险，"
                          "本质上是岸边战事在海上的延伸。")
                en.append(f"Zoom out and the picture is explicit: {labels_en} sit right on this "
                          "waterway — the shipping risk along this line is the maritime face of "
                          "the wars on shore.")
                facts_zh.append(("邻近战区", labels_zh))
                facts_en.append(("Nearby war zones", labels_en))
                heads = []
                for _, z in near:
                    for h in (z.get("headlines") or []):
                        if h.get("title"):
                            heads.append((z.get("label") or "", h))
                # Zone headlines are English data from OSIRIS — EN page only, per the
                # language policy. Echoed exactly as the feed provides them.
                for zlabel, h in heads[:3]:
                    t, u, s = h.get("title", ""), h.get("url", ""), h.get("source", "")
                    val = f'<a href="{u}">{t}</a>' if u else t
                    if s:
                        val += f" — {s}"
                    facts_en.append((f"In the news ({zlabel})" if zlabel else "In the news", val))
                if heads:
                    top = heads[0][1]
                    en.append(f"In the news: \"{top.get('title', '')}\""
                              + (f" ({top.get('source')})" if top.get("source") else "") + ".")

    elif kind in ("space", "flare"):
        s = raw
        if kind == "flare":
            cls = s.get("class")
            zh.append(f"太阳活动方面，记录到一次 {cls} 级耀斑，峰值出现在 {s.get('peak')}。")
            zh.append("强耀斑可能扰动高频通信与定位，规模越大影响越广。")
            en.append(f"In solar activity, a class {cls} flare was logged, peaking at {s.get('peak')}.")
            en.append("Strong flares can disturb HF comms and positioning; bigger class means wider reach.")
            facts_zh = [("耀斑等级", cls), ("峰值", s.get("peak") or "-"), ("开始", s.get("begin") or "-")]
            facts_en = [("Class", cls), ("Peak", s.get("peak") or "-"), ("Begin", s.get("begin") or "-")]
        else:
            zh.append(f"地磁环境当前为 {s.get('storm_level')} 级别（Kp 指数 {s.get('kp_index')}）。")
            zh.append("地磁暴可能影响卫星运行与高纬短波通信。")
            en.append(f"Geomagnetic conditions are {s.get('storm_level')} (Kp {s.get('kp_index')}).")
            en.append("Storms can perturb satellites and high-latitude HF comms.")
            facts_zh = [("级别", s.get("storm_level")), ("Kp 指数", s.get("kp_index")),
                        ("时间戳", s.get("kp_timestamp") or "-")]
            facts_en = [("Level", s.get("storm_level")), ("Kp", s.get("kp_index")),
                        ("Timestamp", s.get("kp_timestamp") or "-")]

    elif kind == "jamming":
        n = len(raw.get("gps_jamming", []))
        zh.append(f"航空追踪显示，当前有 {n} 处 GPS 干扰上报。这会令 ADS-B 定位在局部空域失真。")
        en.append(f"Flight tracking shows {n} active GPS-jamming reports, which degrade ADS-B positioning locally.")
        facts_zh = [("干扰点数量", str(n)), ("数据源", raw.get("source", "opensky"))]
        facts_en = [("Jamming points", str(n)), ("Source", raw.get("source", "opensky"))]

    elif kind == "fire":
        n = raw.get("count", 0)
        hc = raw.get("high_conf", 0)
        frp = raw.get("frp", 0)
        win = raw.get("window", "-")
        top = raw.get("per_country", [])
        top_str = "、".join(f"{c['name']} {c['count']} 处" for c in top[:4])
        top_str_en = ", ".join(f"{c['name']} {c['count']}" for c in top[:4])
        zh.append(f"南部非洲正经历一轮活跃野火季。在 {win} 的观测窗口内，卫星共在该区域捕获 {n} 处火点，"
                  f"其中高置信度 {hc} 处，累计火辐射功率（FRP）约 {frp:.0f} MW。")
        zh.append(f"火点最集中的地区依次为：{top_str}。"
                  "这一带每年 6–10 月为干季，烧荒与自然火叠加，跨境烟雾常影响区域空气质量与航空。")
        en.append(f"Southern Africa is in an active fire season. Within the {win} observation window, "
                  f"satellites detected {n} fire hotspots across the region — {hc} of them high-confidence, "
                  f"with a combined fire radiative power (FRP) of about {frp:.0f} MW.")
        en.append(f"The densest concentrations: {top_str_en}. "
                  "The June–October dry season mixes land-clearing burns with natural fires, and the "
                  "resulting transboundary haze routinely affects regional air quality and aviation.")
        g = raw.get("global_total")
        if g:
            share = 100.0 * n / g if g else 0
            zh.append(f"作为参照，同一窗口内全球火点总数为 {g:,} 处，南部非洲约占 {share:.0f}%。")
            en.append(f"For scale, the same window logged {g:,} detections globally — southern Africa "
                      f"accounts for roughly {share:.0f}% of them.")
        facts_zh = [("火点总数", str(n)), ("高置信度", str(hc)),
                    ("累计辐射功率", f"{frp:.0f} MW"), ("数据窗口", win),
                    ("最集中地区", top_str or "-"),
                    ("数据源", "NASA FIRMS (VIIRS) · osirisai.live")]
        facts_en = [("Detections", str(n)), ("High-confidence", str(hc)),
                    ("Total FRP", f"{frp:.0f} MW"), ("Window", win),
                    ("Most affected", top_str_en or "-"),
                    ("Source", "NASA FIRMS (VIIRS) · osirisai.live")]

    return {"zh": zh, "en": en, "facts_zh": facts_zh, "facts_en": facts_en}


# ------------------------------------------------------------- html

PAGE = """<!DOCTYPE html><html lang="__LANG__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · __BRAND__</title>
<style>
 :root{color-scheme:dark;}
 body{margin:0;background: radial-gradient(1200px 600px at 70% -10%,#13202e,#0b0f14 60%);
   color:#e6edf3;font:15px/1.65 -apple-system,"SF Pro Text",Segoe UI,Roboto,Helvetica,Arial,sans-serif;}
 .wrap{max-width:760px;margin:0 auto;padding:46px 22px 70px;}
 .kicker{color:#4db8ff;font:600 12px/1 ui-monospace,monospace;letter-spacing:1.5px;text-transform:uppercase;}
 h1{font-size:27px;line-height:1.2;margin:8px 0 4px;}
 .date{color:#7d8b9a;font-size:13px;margin-bottom:26px;}
 .lead p{margin:0 0 14px;}
 .facts{width:100%;border-collapse:collapse;margin:22px 0;font-size:14px;}
 .facts td{padding:9px 12px;border-bottom:1px solid #1a222c;vertical-align:top;}
 .facts td:first-child{color:#7d8b9a;width:34%;}
 .note{background:#10172280;border:1px solid #1e2630;border-radius:10px;padding:14px 16px;color:#c2cedb;font-size:13.5px;}
 .note b{color:#e6edf3;}
 .stats{display:flex;flex-wrap:wrap;gap:10px;margin:26px 0 8px;}
 .chip{background:#121821;border:1px solid #1e2630;border-radius:9px;padding:9px 13px;font-size:13px;}
 .chip b{color:#4db8ff;font-variant-numeric:tabular-nums;}
 .others{margin:24px 0 6px;border-top:1px solid #1a222c;padding-top:18px;}
 .others-h{color:#7d8b9a;font:600 12px/1 ui-monospace,monospace;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:10px;}
 .others ul{list-style:none;margin:0;padding:0;}
 .others li{display:flex;align-items:baseline;gap:10px;padding:7px 0;border-bottom:1px solid #131a22;font-size:14px;}
 .others .fam{color:#4db8ff;font-size:12px;min-width:84px;flex:none;}
 .others .ot{color:#c2cedb;flex:1;}
 .others .sc{color:#7d8b9a;font-variant-numeric:tabular-nums;font-size:12.5px;flex:none;}
 .others-note{color:#52606d;font-size:12px;margin-top:10px;}
 a{color:#4db8ff;text-decoration:none;} a:hover{text-decoration:underline;}
 .src{color:#6f7d8b;font-size:12.5px;margin-top:30px;border-top:1px solid #1a222c;padding-top:16px;}
 .lang{position:fixed;top:18px;right:20px;font-size:12.5px;}
 footer{margin-top:30px;color:#52606d;font-size:12px;}
</style></head><body>
<div class="lang"><a href="__OTHER__">__OTHERLABEL__</a></div>
<div class="wrap">
 <div class="kicker">__BRAND__</div>
 <h1>__TITLE__</h1>
 <div class="date">__DATELINE__</div>
 <div class="lead">__BODY__</div>
 <table class="facts">__FACTS__</table>
 <div class="note"><b>__NOTELABEL__</b> __WHY__</div>
 <div class="stats">__CHIPS__</div>
 __OTHERS__
 <div class="src">__SOURCES__</div>
 <footer>__FOOTER__</footer>
</div></body></html>"""

# Extra CSS injected only for urgent/breaking posts (kept separate so the base
# template stays the same for every ordinary day).
URGENT_CSS = """
 .kicker{color:#ff6b6b;}
 .wrap{border-left:3px solid #ff5b5b;box-shadow: inset 0 0 90px #ff00000d;}
 body{background: radial-gradient(1200px 600px at 70% -10%,#2a1216,#0b0f14 60%);}
 .breaking{display:inline-flex;align-items:center;gap:8px;background:#2a0d12;border:1px solid #ff5b5b55;
   color:#ff6b6b;font:700 12px/1 ui-monospace,monospace;letter-spacing:1.5px;text-transform:uppercase;
   padding:7px 12px;border-radius:8px;margin-bottom:14px;}
 .breaking .dot{width:8px;height:8px;border-radius:50%;background:#ff4d4d;box-shadow:0 0 9px #ff4d4d;animation:pulse 1.4s infinite;}
 @keyframes pulse{0%,100%{opacity:1;}50%{opacity:.35;}}
"""


def why_text(story, lang="zh"):
    k = story["kind"]
    zh = {
        "quake": "震级、深度与海啸标志共同决定一次地震的实际影响面，是普通人最容易理解、也最值得长期追踪的全球动态之一。",
        "conflict": "冲突热点直接关联航运咽喉、能源与难民流动，是地缘与市场的早期信号。",
        "conflict_event": "实时战况事件往往先于主流媒体报道，是开源情报的价值所在。",
        "port": "主要港口拥堵会沿供应链放大，影响交付周期与运价，是宏观与贸易的先行指标。",
        "chokepoint": "咽喉要道是全球能源与贸易的单点节点，其风险等级变化先于运价与保费反应，是地缘紧张最直接的量化读数之一。",
        "space": "地磁活动影响卫星与通信，强风暴期间高纬度短波与定位会受扰。",
        "flare": "太阳耀斑可扰动高频通信与 GNSS 定位，规模越大、影响越广。",
        "jamming": "GPS 干扰反映局部空域的异常电磁环境，常与军事活动相关。",
        "fire": "野火季直接牵动区域空气质量、跨境烟雾与生态损失，卫星火点是最早可量化的先行指标之一。",
        "none": "这是当天所有可用信号中显著性最高的一条。",
    }
    en = {
        "quake": "Magnitude, depth and the tsunami flag together determine a quake's real impact — one of the most accessible global signals for ordinary people to track over time.",
        "conflict": "Conflict hotspots are tied directly to shipping chokepoints, energy and migration — early signals for geopolitics and markets.",
        "conflict_event": "Live conflict events often break ahead of mainstream coverage — the core value of open-source intelligence.",
        "port": "Major-port congestion cascades through supply chains, hitting lead times and freight rates — a leading macro and trade indicator.",
        "chokepoint": "Chokepoints are single points of failure for global energy and trade; their risk rating moves ahead of freight rates and war-risk premiums — one of the most direct quantified readings of geopolitical tension.",
        "space": "Geomagnetic activity affects satellites and comms; strong storms disrupt high-latitude HF radio and positioning.",
        "flare": "Solar flares disturb HF comms and GNSS positioning; the larger the class, the wider the reach.",
        "jamming": "GPS jamming reveals an anomalous electromagnetic environment in local airspace, often linked to military activity.",
        "fire": "Fire season drives air quality, transboundary haze and ecological loss — satellite fire detections are one of the earliest quantifiable leading indicators.",
        "none": "This is the single most salient signal among all available feeds for the day.",
    }
    table = zh if lang == "zh" else en
    return table.get(k, table["none"])


def stats_chips(stats, lang="zh"):
    if not isinstance(stats, dict) or "stats" not in stats:
        return ""
    s = stats["stats"]
    labels = {"zh": ("航班", "卫星", "摄像头", "事件"),
              "en": ("Flights", "Satellites", "Cameras", "Incidents")}[lang]
    items = list(zip(labels, (s.get("flights"), s.get("sats"),
                              s.get("cctv"), s.get("incidents"))))
    return "".join(f'<div class="chip"><b>{v:,}</b> {k}</div>' for k, v in items if v is not None)


def sources_line(story, lang="zh"):
    from urllib.parse import urlparse
    raw = story["raw"]
    hosts = []
    for key in ("url", "sourceUrl", "external_url"):
        if isinstance(raw, dict) and raw.get(key):
            try:
                hosts.append(f'<a href="{raw[key]}" target="_blank">{urlparse(raw[key]).netloc}</a>')
            except Exception:
                hosts.append(f'<a href="{raw[key]}" target="_blank">{raw[key]}</a>')
    if not hosts:
        hosts.append('<a href="https://www.osirisai.live" target="_blank">osirisai.live</a>')
    label = "来源：" if lang == "zh" else "Sources: "
    return label + " · ".join(hosts)


def render_others(others, lang):
    """Bilingual snapshot of the day's other notable families (never the headline family)."""
    if not others:
        return ""
    head = {"zh": "今日其他信号", "en": "Other signals today"}[lang]
    fam_label = {
        "quake": {"zh": "地震", "en": "Earthquakes"},
        "conflict": {"zh": "冲突", "en": "Conflicts"},
        "maritime": {"zh": "航运", "en": "Maritime"},
        "space": {"zh": "空间天气", "en": "Space weather"},
        "jamming": {"zh": "航空干扰", "en": "Aviation jamming"},
        "fire": {"zh": "野火", "en": "Wildfires"},
        "other": {"zh": "其他", "en": "Other"},
    }
    items = []
    for c in others:
        fam = c.get("family") or family_of(c["kind"])
        fl = fam_label.get(fam, {"zh": "其他", "en": "Other"})[lang]
        title = c[f"headline_{lang}"]
        sc = c.get("score", 0)
        items.append(
            f'<li><span class="fam">{fl}</span>'
            f'<span class="ot">{title}</span>'
            f'<span class="sc">{sc:.1f}</span></li>'
        )
    note = ("仅作快照，不计入头条评分。" if lang == "zh"
            else "Snapshot only — not part of the headline score.")
    return (f'<div class="others"><div class="others-h">{head}</div>'
            f'<ul>{"".join(items)}</ul>'
            f'<div class="others-note">{note}</div></div>')


def render(lang, story, content, data, date_str, gen_str, others=None, urgent=False, urgent_text=""):
    other = "index.en.html" if lang == "zh" else "index.zh.html"
    other_label = "English" if lang == "zh" else "中文"
    brand = {"zh": "OSIRIS 每日简报", "en": "OSIRIS Daily Brief"}[lang]
    dateline = (f"{date_str} · 生成于 {gen_str} · 数据来自 osirisai.live 公开 feed"
                if lang == "zh"
                else f"{date_str} · generated {gen_str} from public osirisai.live feeds")
    notelabel = "为什么值得记：" if lang == "zh" else "Why it matters: "
    footer = ("OSIRIS 每日简报 — 自动生成。数据来自公开免密钥 feed（USGS、NASA FIRMS、OpenSky、"
              "各国交通监控网络）的瞬时快照，并非权威情报来源。"
              if lang == "zh" else
              "OSIRIS Daily Brief — auto-generated. Figures are a point-in-time snapshot of public "
              "keyless feeds (USGS, NASA FIRMS, OpenSky, national traffic-camera networks). "
              "Not an authoritative intelligence source.")
    body = "".join(f"<p>{p}</p>" for p in content[lang])
    facts = "".join(f"<tr><td>{k}</td><td>{v if v is not None else ''}</td></tr>"
                    for k, v in content[f"facts_{lang}"])
    html = PAGE
    for tok, val in (
        ("__LANG__", lang), ("__TITLE__", story[f"headline_{lang}"]),
        ("__BRAND__", brand), ("__OTHER__", other), ("__OTHERLABEL__", other_label),
        ("__DATELINE__", dateline), ("__BODY__", body),
        ("__FACTS__", facts), ("__NOTELABEL__", notelabel),
        ("__WHY__", why_text(story, lang)),
        ("__CHIPS__", stats_chips(data.get("stats"), lang)),
        ("__OTHERS__", render_others(others or [], lang)),
        ("__SOURCES__", sources_line(story, lang)),
        ("__FOOTER__", footer),
        ("__URGENTTEXT__", urgent_text),
    ):
        html = html.replace(tok, str(val))
    if urgent:
        html = html.replace('<div class="wrap">',
                             '<div class="wrap">\n <div class="breaking"><span class="dot"></span>__URGENTTEXT__</div>', 1)
        html = html.replace('</style>', URGENT_CSS + '\n</style>', 1)
        html = html.replace('__URGENTTEXT__', urgent_text)
    return html


# ------------------------------------------------------------- index

def build_index(posted_days):
    rows = ""
    for day in sorted(posted_days, reverse=True):
        meta_path = os.path.join(ROOT, "posts", day, "meta.json")
        urgent = False
        if os.path.isfile(meta_path):
            try:
                urgent = bool(json.load(open(meta_path, encoding="utf-8")).get("urgent"))
            except Exception:
                pass
        tag = ' <span class="urg">紧急</span>' if urgent else ""
        rows += (f'<tr><td>{day}{tag}</td>'
                 f'<td><a href="posts/{day}/index.zh.html">中文</a> · '
                 f'<a href="posts/{day}/index.en.html">English</a></td></tr>')
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OSIRIS Daily Brief · 归档</title>
<style>
 body{{margin:0;background:#0b0f14;color:#e6edf3;font:15px/1.6 -apple-system,"SF Pro Text",Segoe UI,Roboto,sans-serif;}}
 .wrap{{max-width:720px;margin:0 auto;padding:48px 22px 70px;}}
 h1{{font-size:24px;margin:0 0 4px;}}
 .sub{{color:#7d8b9a;font-size:13px;margin-bottom:26px;}}
 table{{width:100%;border-collapse:collapse;}}
 td{{padding:11px 12px;border-bottom:1px solid #1a222c;}}
 a{{color:#4db8ff;text-decoration:none;}} a:hover{{text-decoration:underline;}}
 .urg{{color:#ff6b6b;font:600 11px/1 ui-monospace,monospace;margin-left:6px;}}
</style></head><body><div class="wrap">
<h1>OSIRIS Daily Brief</h1>
<div class="sub">每日从 osirisai.live 公开 API 自动生成的全球态势简报 · 中英双语</div>
<table><tr><th>日期</th><th>语言</th></tr>{rows}</table>
</div></body></html>"""
    return html


# ------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--urgent", help="Force a story mentioning KEYWORD as an urgent/breaking headline "
                                      "(overrides the algorithm). Falls back to the algorithm if no match.")
    a = ap.parse_args()

    today = a.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    gen_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    recent_families = load_recent_families(os.path.join(ROOT, "posts"), today)
    recent_headlines = load_recent_headlines(os.path.join(ROOT, "posts"), today)

    print(f"[*] fetching OSIRIS feeds for {today} ...")
    data = load_all()
    cands = candidates(data)
    cands = apply_recency(cands, recent_families, recent_headlines)

    # --- urgent override (explicit instruction only; algorithm otherwise untouched) ---
    urgent = bool(a.urgent)
    urgent_story = find_urgent(cands, a.urgent) if urgent else None
    if urgent and urgent_story is None:
        print(f"[!] --urgent '{a.urgent}' matched nothing in current feeds; falling back to the algorithm.")
        urgent = False

    if not cands:
        print("[!] no candidate story found (feeds may be empty/unreachable). Writing a 'no signal' post.")
        story = {
            "kind": "none", "score": 0, "ts": 0, "raw": {},
            "headline_zh": "今日无显著信号", "headline_en": "No notable signal today",
        }
        content = {
            "zh": ["本次抓取未能从公开 feed 中识别出显著事件，可能因上游接口临时不可用。"],
            "en": ["This run found no notable event in the public feeds; upstream APIs may be temporarily unavailable."],
            "facts_zh": [("状态", "无信号")], "facts_en": [("Status", "no signal")],
        }
    else:
        if urgent and urgent_story:
            story = urgent_story
            story["urgent"] = True
            # strip the generic "Live event:" / "实时战况：" prefix for a clean headline
            story["headline_en"] = re.sub(r'^Live event:\s*', '', story["headline_en"])
            story["headline_zh"] = re.sub(r'^实时战况：\s*', '', story["headline_zh"])
            print(f"[*] URGENT override: {story['headline_en']}")
            content = narrate(story, data)
            content["en"].insert(0, "This is a fast-moving situation — the details below are a point-in-time snapshot from public OSIRIS feeds.")
            content["zh"].insert(0, "⚠ 这是一则突发进展，以下内容为公开 OSIRIS feed 在当前时间点的快照。")
        else:
            story = pick_story(cands, recent_families)
            # Auto-breaking rule (user, 2026-10-23): NOT every conflict is breaking
            # — only those with new developments. A live conflict_event is itself a
            # new development; a standing zone counts as new only if OSIRIS attached
            # fresh headlines (news of movement) to it. Natural disasters etc. stay
            # normal unless their impact is large.
            def _has_new_dev(c):
                raw = c.get("raw") or {}
                return bool([h for h in (raw.get("headlines") or []) if h.get("title")])
            auto_break = (story["kind"] == "conflict_event") or (
                story["kind"] == "conflict" and _has_new_dev(story))
            if auto_break and not urgent:
                urgent = True
                story["urgent"] = True
                story["headline_en"] = re.sub(r'^Live event:\s*', '', story["headline_en"])
                story["headline_zh"] = re.sub(r'^实时战况：\s*', '', story["headline_zh"])
                content = narrate(story, data)
                content["en"].insert(0, "This is a fast-moving situation — the details below are a point-in-time snapshot from public OSIRIS feeds.")
                content["zh"].insert(0, "⚠ 这是一则突发进展，以下内容为公开 OSIRIS feed 在当前时间点的快照。")
                print(f"[*] CONFLICT auto-breaking: {story['headline_en']}")
            else:
                pen = story.get("penalty", 0)
                lpen = story.get("label_penalty", 0)
                switched = story is not cands[0]
                tag = f" (raw {story['score']}, -{pen} family, -{lpen} same-headline)"
                tag += " [family-switch: recent top family repeated]" if switched else ""
                print(f"[*] picked: {story['headline_en']} (adj {story['adj']}{tag})")
                content = narrate(story, data)

    # "other signals" snapshot: the top candidate of every family except the chosen one
    others = []
    chosen_fam = story.get("family") or family_of(story["kind"])
    if cands:
        best = {}
        for c in cands:
            fam = c.get("family") or family_of(c["kind"])
            if fam == chosen_fam:
                continue
            if fam not in best or c["adj"] > best[fam]["adj"]:
                best[fam] = c
        others = sorted(best.values(), key=lambda x: -x["adj"])[:6]

    out_dir = os.path.join(ROOT, "posts", today)
    os.makedirs(out_dir, exist_ok=True)
    is_urgent = bool(story.get("urgent"))
    zh = render("zh", story, content, data, today, gen_str, others, urgent=is_urgent, urgent_text="紧急")
    en = render("en", story, content, data, today, gen_str, others, urgent=is_urgent, urgent_text="BREAKING")
    with open(os.path.join(out_dir, "index.zh.html"), "w", encoding="utf-8") as f:
        f.write(zh)
    with open(os.path.join(out_dir, "index.en.html"), "w", encoding="utf-8") as f:
        f.write(en)

    # persist a small metadata record so future runs can avoid repeating the same family
    meta = {
        "date": today,
        "family": story.get("family", family_of(story["kind"])),
        "kind": story["kind"],
        "headline_en": story["headline_en"],
        "headline_zh": story["headline_zh"],
        "score": story["score"],
        "adj": story.get("adj", story["score"]),
        "urgent": bool(story.get("urgent", False)),
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # rebuild master index from whatever day-folders exist
    days = sorted(d for d in os.listdir(os.path.join(ROOT, "posts"))
                  if os.path.isfile(os.path.join(ROOT, "posts", d, "index.zh.html")))
    with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index(days))

    print(f"[+] wrote posts/{today}/index.zh.html + index.en.html  ({len(days)} days indexed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
