#!/usr/bin/env python3
"""
fix_language.py — one-off language-unification pass over already-published posts.

The generator used to bake language-mixed chrome into each page:
  * EN pages carried Chinese note-label + Chinese "why" text + Chinese stat-chip labels.
  * ZH pages carried English brand, English date line, "Sources:" and English footer.
  * ZH pages also carried the raw English OSIRIS description and English time strings.

This script rewrites ONLY the chrome/localisation in the existing HTML so every
post is consistently EN-only (index.en.html) / ZH-only (index.zh.html). It does
NOT touch the underlying story data (which we no longer have), so historical
posts keep their original content while gaining clean, language-pure framing.

Safe to run more than once (idempotent). Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))

# Bilingual "why it matters" text, keyed by story kind (matches generate_brief.why_text).
WHY_ZH = {
    "quake": "震级、深度与海啸标志共同决定一次地震的实际影响面，是普通人最容易理解、也最值得长期追踪的全球动态之一。",
    "conflict": "冲突热点直接关联航运咽喉、能源与难民流动，是地缘与市场的早期信号。",
    "conflict_event": "实时战况事件往往先于主流媒体报道，是开源情报的价值所在。",
    "port": "主要港口拥堵会沿供应链放大，影响交付周期与运价，是宏观与贸易的先行指标。",
    "space": "地磁活动影响卫星与通信，强风暴期间高纬度短波与定位会受扰。",
    "flare": "太阳耀斑可扰动高频通信与 GNSS 定位，规模越大、影响越广。",
    "jamming": "GPS 干扰反映局部空域的异常电磁环境，常与军事活动相关。",
    "none": "这是当天所有可用信号中显著性最高的一条。",
}
WHY_EN = {
    "quake": "Magnitude, depth and the tsunami flag together determine a quake's real impact — one of the most accessible global signals for ordinary people to track over time.",
    "conflict": "Conflict hotspots are tied directly to shipping chokepoints, energy and migration — early signals for geopolitics and markets.",
    "conflict_event": "Live conflict events often break ahead of mainstream coverage — the core value of open-source intelligence.",
    "port": "Major-port congestion cascades through supply chains, hitting lead times and freight rates — a leading macro and trade indicator.",
    "space": "Geomagnetic activity affects satellites and comms; strong storms disrupt high-latitude HF radio and positioning.",
    "flare": "Solar flares disturb HF comms and GNSS positioning; the larger the class, the wider the reach.",
    "jamming": "GPS jamming reveals an anomalous electromagnetic environment in local airspace, often linked to military activity.",
    "none": "This is the single most salient signal among all available feeds for the day.",
}

FOOTER_ZH = ("OSIRIS 每日简报 — 自动生成。数据来自公开免密钥 feed（USGS、NASA FIRMS、OpenSky、"
             "各国交通监控网络）的瞬时快照，并非权威情报来源。")
BRAND_ZH = "OSIRIS 每日简报"
BRAND_EN = "OSIRIS Daily Brief"

CHIP_ZH_TO_EN = [(" 航班</div>", " Flights</div>"), (" 卫星</div>", " Satellites</div>"),
                 (" 摄像头</div>", " Cameras</div>"), (" 事件</div>", " Incidents</div>")]


def host_from_href(m):
    try:
        return f'<a href="{m.group(1)}">{urlparse(m.group(1)).netloc}</a>'
    except Exception:
        return m.group(0)


def fix_en(html, kind):
    # 1) note label ZH -> EN
    html = html.replace('<b>为什么值得记：</b>', '<b>Why it matters: </b>')
    # 2) note body: swap the Chinese rationale for its English equivalent.
    #    Swap EVERY known (zh -> en) pair so a mislabeled meta kind can't leave
    #    Chinese text behind on the English page.
    for k in WHY_ZH:
        html = html.replace(WHY_ZH[k], WHY_EN[k])
    # 3) stat-chip labels ZH -> EN
    for z, e in CHIP_ZH_TO_EN:
        html = html.replace(z, e)
    # 4) redundant kicker "OSIRIS Daily Brief · Daily Brief" -> brand
    html = html.replace(f"{BRAND_EN} · Daily Brief", BRAND_EN)
    # 5) link text: replace raw key names with the host (both languages)
    html = re.sub(r'<a href="([^"]+)">(?:url|sourceUrl|external_url)</a>', host_from_href, html)
    # 6) title/brand already EN — leave as is
    return html


def fix_zh(html):
    # 1) kicker brand EN -> ZH
    html = html.replace(f"{BRAND_EN} · 每日简报", BRAND_ZH)
    html = html.replace(f"{BRAND_EN} · Daily Brief", BRAND_EN)  # safety
    # 2) title suffix EN -> ZH
    html = html.replace(f" · {BRAND_EN}</title>", f" · {BRAND_ZH}</title>")
    # 3) date line EN -> ZH (capture date + generated timestamp)
    html = re.sub(
        r'<div class="date">(.*?) · generated (.*?) from public osirisai\.live feeds</div>',
        r'<div class="date">\1 · 生成于 \2 · 数据来自 osirisai.live 公开 feed</div>',
        html)
    # 4) sources label EN -> ZH
    html = html.replace('Sources: ', '来源：')
    # 5) link text: replace raw key names with the host
    html = re.sub(r'<a href="([^"]+)">(?:url|sourceUrl|external_url)</a>', host_from_href, html)
    # 6) footer EN -> ZH
    html = re.sub(r'<footer>.*?</footer>', f'<footer>{FOOTER_ZH}</footer>', html, flags=re.S)
    # 7) English time strings -> Chinese (only the O.ago() output leaks these)
    html = re.sub(r'(\d+(?:\.\d+)?)h ago', r'\1 小时前', html)
    html = re.sub(r'(\d+(?:\.\d+)?)d ago', r'\1 天前', html)
    html = re.sub(r'(\d+(?:\.\d+)?)m ago', r'\1 分钟前', html)
    # 8) drop the raw English source description row that was shown on ZH pages
    html = re.sub(r'<tr><td>原描述\(EN\)</td><td>.*?</td></tr>', '', html)
    return html


def main():
    posts_dir = os.path.join(ROOT, "posts")
    changed = 0
    for day in sorted(os.listdir(posts_dir)):
        d = os.path.join(posts_dir, day)
        zh_path = os.path.join(d, "index.zh.html")
        en_path = os.path.join(d, "index.en.html")
        meta_path = os.path.join(d, "meta.json")
        if not (os.path.isfile(zh_path) and os.path.isfile(en_path)):
            continue
        kind = "none"
        if os.path.isfile(meta_path):
            try:
                kind = json.load(open(meta_path, encoding="utf-8")).get("kind", "none")
            except Exception:
                pass

        zh0 = open(zh_path, encoding="utf-8").read()
        en0 = open(en_path, encoding="utf-8").read()
        zh1 = fix_zh(zh0)
        en1 = fix_en(en0, kind)
        if zh1 != zh0:
            open(zh_path, "w", encoding="utf-8").write(zh1)
            changed += 1
        if en1 != en0:
            open(en_path, "w", encoding="utf-8").write(en1)
            changed += 1
        print(f"[*] {day}: zh={zh1 != zh0} en={en1 != en0} (kind={kind})")
    print(f"[+] done; {changed} file(s) rewritten.")


if __name__ == "__main__":
    main()
