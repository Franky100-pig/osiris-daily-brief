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
    }


# ------------------------------------------------------------- story pick

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

    out.sort(key=lambda x: (-x["score"], -x.get("ts", 0)))
    return out


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
        when = O.ago(e.get("time", 0))
        zh.append(f"今日最值得记录的地球动态，是一次里氏 {m:.1f} 级地震，发生于{place}，约 {when}。")
        zh.append("地震本身是板块运动的常态，但震级、震源深度与是否触发海啸预警，决定了它是否值得普通人关注。")
        en.append(f"The headline event today is a M{m:.1f} earthquake near {place}, roughly {when} ago.")
        en.append("Quakes are routine tectonics; magnitude, depth and any tsunami flag are what make one worth a mention.")
        facts_zh = [("位置", place), ("震级", f"M{m:.1f}"), ("深度", f"{e.get('depth', 0):.0f} km"),
                    ("发生时间", when), ("海啸", "是" if e.get("tsunami") else "否"),
                    ("USGS 详情", e.get("url", ""))]
        facts_en = [("Location", place), ("Magnitude", f"M{m:.1f}"), ("Depth", f"{e.get('depth', 0):.0f} km"),
                    ("When", when), ("Tsunami", "yes" if e.get("tsunami") else "no"),
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
            facts_zh.append(("原描述(EN)", desc))
            facts_en.append(("Description", desc))

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

    return {"zh": zh, "en": en, "facts_zh": facts_zh, "facts_en": facts_en}


# ------------------------------------------------------------- html

PAGE = """<!DOCTYPE html><html lang="__LANG__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · OSIRIS Daily Brief</title>
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
 a{color:#4db8ff;text-decoration:none;} a:hover{text-decoration:underline;}
 .src{color:#6f7d8b;font-size:12.5px;margin-top:30px;border-top:1px solid #1a222c;padding-top:16px;}
 .lang{position:fixed;top:18px;right:20px;font-size:12.5px;}
 footer{margin-top:30px;color:#52606d;font-size:12px;}
</style></head><body>
<div class="lang"><a href="__OTHER__">__OTHERLABEL__</a></div>
<div class="wrap">
 <div class="kicker">OSIRIS Daily Brief · __KIND__</div>
 <h1>__TITLE__</h1>
 <div class="date">__DATE__ · generated __GEN__ from public osirisai.live feeds</div>
 <div class="lead">__BODY__</div>
 <table class="facts">__FACTS__</table>
 <div class="note"><b>为什么值得记：</b> __WHY__</div>
 <div class="stats">__CHIPS__</div>
 <div class="src">Sources: __SOURCES__</div>
 <footer>OSIRIS Daily Brief — auto-generated. Figures are a point-in-time snapshot of public keyless feeds
 (USGS, NASA FIRMS, OpenSky, national traffic-camera networks). Not an authoritative intelligence source.</footer>
</div></body></html>"""


def why_text(story):
    k = story["kind"]
    base = {
        "quake": "震级、深度与海啸标志共同决定一次地震的实际影响面，是普通人最容易理解、也最值得长期追踪的全球动态之一。",
        "conflict": "冲突热点直接关联航运咽喉、能源与难民流动，是地缘与市场的早期信号。",
        "conflict_event": "实时战况事件往往先于主流媒体报道，是开源情报的价值所在。",
        "port": "主要港口拥堵会沿供应链放大，影响交付周期与运价，是宏观与贸易的先行指标。",
        "space": "地磁活动影响卫星与通信，强风暴期间高纬度短波与定位会受扰。",
        "flare": "太阳耀斑可扰动高频通信与 GNSS 定位，规模越大、影响越广。",
        "jamming": "GPS 干扰反映局部空域的异常电磁环境，常与军事活动相关。",
    }
    return base.get(k, "这是当天所有可用信号中显著性最高的一条。")


def stats_chips(stats):
    if not isinstance(stats, dict) or "stats" not in stats:
        return ""
    s = stats["stats"]
    items = [("航班", s.get("flights")), ("卫星", s.get("sats")),
             ("摄像头", s.get("cctv")), ("事件", s.get("incidents"))]
    return "".join(f'<div class="chip"><b>{v:,}</b> {k}</div>' for k, v in items if v is not None)


def sources_line(story):
    raw = story["raw"]
    urls = []
    for key in ("url", "sourceUrl", "external_url"):
        if isinstance(raw, dict) and raw.get(key):
            urls.append(f'<a href="{raw[key]}" target="_blank">{key}</a>')
    if not urls:
        urls.append('<a href="https://www.osirisai.live" target="_blank">osirisai.live</a>')
    return " · ".join(urls)


def render(lang, story, content, data, date_str, gen_str):
    kind_label = {"zh": "每日简报", "en": "Daily Brief"}[lang]
    other = "index.en.html" if lang == "zh" else "index.zh.html"
    other_label = "English" if lang == "zh" else "中文"
    body = "".join(f"<p>{p}</p>" for p in content[lang])
    facts = "".join(f"<tr><td>{k}</td><td>{v if v is not None else ''}</td></tr>"
                    for k, v in content[f"facts_{lang}"])
    html = PAGE
    for tok, val in (
        ("__LANG__", lang), ("__TITLE__", story[f"headline_{lang}"]),
        ("__KIND__", kind_label), ("__OTHER__", other), ("__OTHERLABEL__", other_label),
        ("__DATE__", date_str), ("__GEN__", gen_str), ("__BODY__", body),
        ("__FACTS__", facts), ("__WHY__", why_text(story)),
        ("__CHIPS__", stats_chips(data.get("stats"))),
        ("__SOURCES__", sources_line(story)),
    ):
        html = html.replace(tok, str(val))
    return html


# ------------------------------------------------------------- index

def build_index(posted_days):
    rows = ""
    for day in sorted(posted_days, reverse=True):
        rows += (f'<tr><td>{day}</td>'
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
    a = ap.parse_args()

    today = a.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    gen_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"[*] fetching OSIRIS feeds for {today} ...")
    data = load_all()
    cands = candidates(data)
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
        story = cands[0]
        print(f"[*] picked: {story['headline_en']} (score {story['score']})")
        content = narrate(story, data)

    out_dir = os.path.join(ROOT, "posts", today)
    os.makedirs(out_dir, exist_ok=True)
    zh = render("zh", story, content, data, today, gen_str)
    en = render("en", story, content, data, today, gen_str)
    with open(os.path.join(out_dir, "index.zh.html"), "w", encoding="utf-8") as f:
        f.write(zh)
    with open(os.path.join(out_dir, "index.en.html"), "w", encoding="utf-8") as f:
        f.write(en)

    # rebuild master index from whatever day-folders exist
    days = sorted(d for d in os.listdir(os.path.join(ROOT, "posts"))
                  if os.path.isfile(os.path.join(ROOT, "posts", d, "index.zh.html")))
    with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index(days))

    print(f"[+] wrote posts/{today}/index.zh.html + index.en.html  ({len(days)} days indexed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
