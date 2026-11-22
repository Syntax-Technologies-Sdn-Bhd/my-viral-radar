#!/usr/bin/env python3
"""
Viral Posts Dashboard Generator
--------------------------------
Pulls the posts table from Supabase and bakes a static dashboard into
docs/index.html for GitHub Pages.

- Videos play inline via Instagram's official /embed/ iframe (never expires).
- Status writes go straight to Supabase from the browser using the anon key
  (baked into the page). RLS + a column-level grant limit anon to updating
  the status column only, so the key is safe to publish — no login needed.

Env: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from script_rules import clean_script

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")  # public key, baked into the page
# Set on GitHub → Settings → Secrets and variables → Actions → Variables.
# Falls back to sensible defaults so the page still builds before they're set.
BRAND = os.environ.get("BRAND") or "爆款雷达"
SYNC_DAY = os.environ.get("SYNC_DAY") or "每周"
TRACKER_LABEL = {"IG": BRAND + " 竞对", "FB": BRAND + " 竞对", "XHS": BRAND + " 竞对"}
STATUSES = ["未处理", "拍摄中", "已处理", "跳过"]

# comment-bait CTA patterns — require an explicit gated keyword:
# 留言「X」 / comment "X" / comment WORD below / comment the word X / drop a comment / type "X"
BAIT_RE = re.compile(
    r"留言"
    r"|(?i:comment)\s*[\"'“”‘’『「【]"        # comment "X"
    r"|(?i:comment)\s+[A-Z0-9]{2,}\b"          # comment WORD (all-caps keyword)
    r"|(?i:comment\s+the\s+word)\b"            # comment the word X
    r"|(?i:comment\s+\S{1,20}\s+below)\b"      # comment X below
    r"|(?i:drop\s+a\s+comment)"
    r"|(?i:type)\s*[\"'“”‘’]?\S{1,20}[\"'“”‘’]?(?i:\s+below)\b",
)

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")
COMPANIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "competitors.json")
PLATFORM_KEYS = ("website", "facebook", "xiaohongshu", "instagram")


def load_companies():
    """Company directory from competitors.json (name + website/Facebook/小红书/IG).

    Missing or broken file -> empty list, so the dashboard still builds and
    simply falls back to grouping posts by IG handle.
    """
    try:
        with open(COMPANIES_PATH, encoding="utf-8") as f:
            raw = json.load(f).get("companies") or []
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"  WARNING: cannot read competitors.json ({exc}) - no company directory")
        return []
    out = []
    for c in raw:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        item = {"name": name, "self": bool(c.get("self"))}
        for k in PLATFORM_KEYS:
            item[k] = (c.get(k) or "").strip()
        item["instagram"] = _handle(item["instagram"])
        out.append(item)
    return out


def fetch_posts(key):
    """Fetch all rows from the Supabase posts table (paginated)."""
    rows, offset, page = [], 0, 1000
    while True:
        url = f"{SUPABASE_URL}/rest/v1/posts?select=*&order=viral_score.desc.nullslast"
        req = urllib.request.Request(url, headers={
            "apikey": key,
            "Range-Unit": "items", "Range": f"{offset}-{offset+page-1}",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            batch = json.loads(resp.read())
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return rows


def _handle(name):
    """Normalise a competitor handle exactly like comp_key() in sync_content.py,
    so the two scripts agree on what '@Nike', 'instagram.com/nike/',
    'facebook.com/SQLEstream/' and a 小红书 profile URL mean."""
    n = (name or "").strip()
    low = n.lower()
    if "facebook.com" in low:
        u = urllib.parse.urlparse(n if "//" in n else "https://" + n)
        parts = [p for p in u.path.split("/") if p]
        if parts and parts[0] == "profile.php":
            return (urllib.parse.parse_qs(u.query).get("id") or [""])[0]
        return parts[0].lower() if parts else ""
    if "xiaohongshu.com" in low or "rednote.com" in low:
        parts = [p for p in urllib.parse.urlparse(n if "//" in n else "https://" + n).path.split("/") if p]
        if "profile" in parts and parts.index("profile") + 1 < len(parts):
            return parts[parts.index("profile") + 1].lower()
        return ""
    if "instagram.com" in low:
        parts = [p for p in urllib.parse.urlparse(n).path.split("/") if p]
        if not parts or parts[0] in ("p", "reel", "reels", "stories", "tv"):
            return ""
        n = parts[0]
    return n.lstrip("@").strip().lower()


TRACKER_PLAT = {"IG": "instagram", "FB": "facebook", "XHS": "xiaohongshu"}


def account_link(plat, key):
    """(label, profile URL) for the account a post came from."""
    if plat == "facebook":
        return key, f"https://www.facebook.com/{key}"
    if plat == "xiaohongshu":
        return "小红书 " + key[:6], f"https://www.xiaohongshu.com/user/profile/{key}"
    return "@" + key, f"https://www.instagram.com/{key}/"


def fetch_active_handles(key):
    """Handles ticked `active` in the competitors table.

    Returns None when the table cannot be read or is empty. The caller then
    skips filtering, so a key problem or an older schema still builds the full
    dashboard instead of an empty one.
    """
    url = f"{SUPABASE_URL}/rest/v1/competitors?select=username,active"
    req = urllib.request.Request(url, headers={"apikey": key})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            rows = json.loads(resp.read())
    except Exception as exc:
        print(f"  WARNING: cannot read the competitors table ({exc}) - "
              "showing every account that has posts")
        return None
    if not rows:
        print("  WARNING: the competitors table is empty - "
              "showing every account that has posts")
        return None
    return {h for h in (_handle(r.get("username"))
                        for r in rows if r.get("active")) if h}


def normalize(r):
    post_id = str(r.get("post_id") or "").strip()
    if not post_id:
        return None
    caption = (r.get("caption") or "")[:1000]
    tr = (r.get("transcript") or "")
    brk = (r.get("ai_breakdown") or "")
    er = r.get("engagement_rate") or 0
    plat = TRACKER_PLAT.get(r.get("tracker"), "instagram")
    who, who_url = account_link(plat, r.get("competitor") or "")
    return {
        "id": post_id,
        "plat": plat,
        "who": who,
        "whoUrl": who_url,
        "thumb": r.get("thumbnail_url") or "",
        "tracker": TRACKER_LABEL.get(r.get("tracker"), r.get("tracker") or "AI Competitors"),
        "competitor": r.get("competitor") or "",
        "caption": caption,
        "transcript": tr if tr not in ("(转录失败)", "(视频不可用)", "(无口播内容)") else "",
        "brk": "" if brk.strip() == "(拆解失败)" else brk[:600],
        "my": "" if (r.get("my_script") or "").strip() == "(改写失败)" else clean_script(r.get("my_script") or "")[:4000],
        "tags": r.get("hashtags") or "",
        "bait": bool(BAIT_RE.search(caption)),
        "type": r.get("post_type") or "",
        "likes": r.get("likes") or 0,
        "comments": r.get("comments") or 0,
        "er": round(float(er) * 100, 2),
        "followers": r.get("followers") or 0,
        "date": r.get("post_date") or "",
        "url": r.get("post_url") or f"https://www.instagram.com/p/{post_id}/",
        "video": bool(r.get("is_video")) or (r.get("post_type") in ("Video", "Reel")) or bool(r.get("video_url")),
        "ratio": 0,
        "score": float(r.get("viral_score") or 0),
        "status": r.get("status") or "未处理",
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>__BRAND__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0d13;
    --card: #11151d;
    --card-hi: #151a24;
    --border: #1d2430;
    --border-hi: #2c3546;
    --text: #e8ecf3;
    --muted: #8a94a6;
    --faint: #5b6577;
    --accent: #5ea1ff;
    --green: #3fd68f;
    --orange: #f0a33f;
    --pink: #ff6b9d;
    --red: #ff5c5c;
    --fire1: #ff8a3d;
    --fire2: #ff4d6d;
    --font: 'Inter', -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    --disp: 'Space Grotesk', 'Inter', -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scrollbar-color: #232c3b var(--bg); }
  body { background: var(--bg); color: var(--text); font-family: var(--font);
    -webkit-font-smoothing: antialiased; }
  ::selection { background: rgba(94,161,255,.3); }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: #232c3b; border-radius: 8px; }
  ::-webkit-scrollbar-thumb:hover { background: #2c3546; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 6px; }

  .ic { width: 14px; height: 14px; stroke: currentColor; stroke-width: 2;
    stroke-linecap: round; stroke-linejoin: round; fill: none; flex: none; }

  header { position: sticky; top: 0; z-index: 50; background: rgba(10,13,19,.86);
    backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    border-bottom: 1px solid var(--border); padding: 13px 20px 12px; }
  .hrow { display: flex; flex-wrap: wrap; gap: 9px; align-items: center; max-width: 1440px; margin: 0 auto; }
  .brand { display: flex; align-items: center; gap: 10px; margin-right: auto; }
  .mark { width: 31px; height: 31px; border-radius: 9px; display: grid; place-items: center;
    background: linear-gradient(135deg, var(--fire1), var(--fire2));
    box-shadow: 0 4px 16px rgba(255,92,80,.35); }
  .mark .ic { width: 17px; height: 17px; color: #fff; }
  h1 { font-family: var(--disp); font-size: 16.5px; font-weight: 700; letter-spacing: .2px; white-space: nowrap; line-height: 1.15; }
  h1 small { display: block; color: var(--faint); font-family: var(--font); font-weight: 400;
    font-size: 10.5px; letter-spacing: 0; }

  .search { position: relative; }
  .search > .ic { position: absolute; left: 11px; top: 50%; transform: translateY(-50%);
    color: var(--faint); width: 13.5px; pointer-events: none; }
  input#fSearch { background: var(--card); color: var(--text); border: 1px solid var(--border);
    border-radius: 10px; padding: 8px 12px 8px 32px; font-size: 13px; font-family: var(--font);
    width: 200px; transition: border-color .18s, background .18s; }
  input#fSearch:hover { border-color: var(--border-hi); }
  input#fSearch:focus { border-color: var(--accent); outline: none; }
  input#fSearch::placeholder { color: var(--faint); }

  select, .btn { background: var(--card); color: var(--text); border: 1px solid var(--border);
    border-radius: 10px; padding: 8px 11px; font-size: 13px; font-family: var(--font);
    cursor: pointer; transition: border-color .18s, background .18s, color .18s;
    display: inline-flex; align-items: center; gap: 6px; }
  select:hover, .btn:hover { border-color: var(--border-hi); background: var(--card-hi); }
  .btn:active { transform: scale(.97); }
  .btn.on { border-color: var(--accent); color: var(--accent); background: rgba(94,161,255,.08); }

  .tabs { display: flex; gap: 7px; flex-wrap: wrap; max-width: 1440px; margin: 12px auto 0; }
  .tab { display: inline-flex; align-items: center; gap: 7px; background: var(--card);
    border: 1px solid var(--border); border-radius: 999px; padding: 6.5px 14px;
    font-size: 13px; color: var(--muted); cursor: pointer; user-select: none;
    transition: border-color .18s, background .18s, color .18s, transform .18s; }
  .tab:hover { border-color: var(--border-hi); color: var(--text); }
  .tab .cnt { font-family: var(--disp); font-weight: 600; font-size: 11px;
    background: rgba(255,255,255,.06); padding: 1.5px 7.5px; border-radius: 99px;
    font-variant-numeric: tabular-nums; }
  .tab.on { color: var(--text);
    background: linear-gradient(135deg, rgba(255,138,61,.15), rgba(255,77,109,.15));
    border-color: rgba(255,120,90,.45); }
  .tab.on .cnt { background: rgba(255,120,90,.25); }
  body.dragging .tab.droppable { border-style: dashed; border-color: var(--accent); }
  .tab.over { background: rgba(94,161,255,.22); color: var(--text); transform: scale(1.07); }

  .chips { display: flex; gap: 6px; flex-wrap: wrap; max-width: 1440px; margin: 9px auto 0; align-items: center; }
  .chips .lbl { font-size: 12px; color: var(--faint); margin-right: 2px; }
  .chip2 { display: inline-flex; align-items: center; gap: 5.5px; background: var(--card);
    border: 1px solid var(--border); border-radius: 999px; padding: 4.5px 12px;
    font-size: 12px; color: var(--muted); cursor: pointer; user-select: none;
    transition: border-color .18s, color .18s, background .18s;
    font-variant-numeric: tabular-nums; }
  .chip2 .ic { width: 12.5px; height: 12.5px; }
  .chip2:hover { border-color: var(--border-hi); color: var(--text); }
  .chip2.on { border-color: rgba(240,163,63,.6); color: var(--orange); background: rgba(240,163,63,.08); }

  main { max-width: 1440px; margin: 22px auto; padding: 0 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(305px, 1fr)); gap: 18px; }

  .card { position: relative; background: var(--card); border: 1px solid var(--border);
    border-radius: 16px; overflow: hidden; display: flex; flex-direction: column;
    transition: transform .22s cubic-bezier(.2,.7,.3,1), border-color .22s, box-shadow .22s;
    animation: fadeUp .45s cubic-bezier(.2,.7,.3,1) both; }
  .card:hover { transform: translateY(-3px); border-color: var(--border-hi);
    box-shadow: 0 12px 32px rgba(0,0,0,.45); }
  .card.outlier::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, var(--fire1), var(--fire2)); z-index: 2; }
  .card[draggable] { cursor: grab; }
  .card.lifting { opacity: .45; }
  .card.saved { border-color: var(--green); }
  @keyframes fadeUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }

  .embed { position: relative; width: 100%; aspect-ratio: 4/5; background: #080b10; }
  .embed iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
  .embed .ph { position: absolute; inset: 0; display: grid; place-items: center;
    background: linear-gradient(110deg, #0b0e15 8%, #121826 18%, #0b0e15 33%);
    background-size: 200% 100%; animation: shimmer 1.5s linear infinite; }
  .embed .ph .ic { width: 30px; height: 30px; color: #2a3342; stroke-width: 1.6; }
  @keyframes shimmer { to { background-position-x: -200%; } }

  .meta { padding: 13px 15px 14px; display: flex; flex-direction: column; gap: 9px; }
  .row1 { display: flex; align-items: center; gap: 7px; font-size: 13px; flex-wrap: wrap; }
  .handle { color: #3a4456; cursor: grab; display: inline-flex; }
  .handle .ic { width: 13px; height: 13px; }
  .who { font-weight: 600; color: var(--accent); text-decoration: none; }
  .who:hover { text-decoration: underline; }
  .badge { display: inline-flex; align-items: center; gap: 4px; font-size: 10.5px; font-weight: 500;
    padding: 2.5px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }
  .badge .ic { width: 10.5px; height: 10.5px; }
  .badge.bnew { color: var(--pink); border-color: rgba(255,107,157,.45); background: rgba(255,107,157,.07); }
  .badge.bhorse { color: var(--orange); border-color: rgba(240,163,63,.45); background: rgba(240,163,63,.07); }
  .badge.bbait { color: var(--accent); border-color: rgba(94,161,255,.4); background: rgba(94,161,255,.07); }
  .date { margin-left: auto; color: var(--faint); font-size: 11px; font-variant-numeric: tabular-nums; }

  .nums { display: flex; gap: 6px; flex-wrap: wrap; }
  .num { display: inline-flex; align-items: center; gap: 5px; background: #0c0f16;
    border: 1px solid var(--border); border-radius: 9px; padding: 5px 9px;
    font-size: 11px; color: var(--muted); }
  .num .ic { width: 11.5px; height: 11.5px; }
  .num b { font-family: var(--disp); color: var(--text); font-size: 12.5px; font-weight: 600;
    font-variant-numeric: tabular-nums; }
  .num.hot { border-color: rgba(255,122,77,.35); }
  .num.hot .ic, .num.hot b { color: #ff7a4d; }
  .num.x { border-color: rgba(240,163,63,.35); }
  .num.x .ic, .num.x b { color: var(--orange); }
  .num.er .ic, .num.er b { color: var(--green); }

  .cap { color: var(--muted); font-size: 12.5px; line-height: 1.55; display: -webkit-box;
    -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; cursor: pointer; }
  .cap.open { -webkit-line-clamp: unset; }
  .cap b { color: var(--text); font-weight: 600; }
  .ai { border-left: 2px solid var(--orange); background: rgba(240,163,63,.06);
    padding: 8px 11px; border-radius: 0 10px 10px 0; font-size: 12px; line-height: 1.65;
    color: var(--muted); }
  .ai b { color: var(--orange); font-weight: 600; }
  .mine { border-left: 2px solid var(--green); background: rgba(63,214,143,.06);
    padding: 8px 11px; border-radius: 0 10px 10px 0; font-size: 12px; line-height: 1.65;
    color: var(--muted); cursor: pointer; display: -webkit-box; -webkit-line-clamp: 4;
    -webkit-box-orient: vertical; overflow: hidden; white-space: pre-wrap; }
  .mine.open { -webkit-line-clamp: unset; }
  .mine b { color: var(--green); font-weight: 600; }

  .srow { display: flex; align-items: center; gap: 7px; }
  .srow label { font-size: 12px; color: var(--faint); }
  .status { flex: 1; padding: 6px 9px; font-size: 12.5px; }
  .status.s未处理 { color: var(--muted); }
  .status.s拍摄中 { color: var(--orange); border-color: rgba(240,163,63,.5); }
  .status.s已处理 { color: var(--green); border-color: rgba(63,214,143,.5); }
  .status.s跳过 { color: var(--red); border-color: rgba(255,92,92,.5); }
  .abtn { display: inline-flex; align-items: center; gap: 5px; background: #0c0f16;
    color: var(--muted); border: 1px solid var(--border); border-radius: 9px;
    padding: 6px 10px; font-size: 11.5px; font-family: var(--font); cursor: pointer;
    text-decoration: none; transition: color .18s, border-color .18s, transform .1s; }
  .abtn .ic { width: 12px; height: 12px; }
  .abtn:hover { color: var(--accent); border-color: rgba(94,161,255,.5); }
  .abtn:active { transform: scale(.95); }

  .more { text-align: center; margin: 28px 0 44px; }
  .more button { background: var(--card); color: var(--accent); border: 1px solid var(--border);
    border-radius: 12px; padding: 11px 30px; font-size: 14px; font-family: var(--font);
    cursor: pointer; transition: border-color .18s, background .18s; }
  .more button:hover { border-color: var(--border-hi); background: var(--card-hi); }

  .empty { color: var(--faint); text-align: center; padding: 70px 0; display: flex;
    flex-direction: column; align-items: center; gap: 12px; font-size: 14px; }
  .empty .ic { width: 34px; height: 34px; stroke-width: 1.5; color: #2c3546; }

  /* 竞对名录 — company directory with per-platform links */
  #dir { display: none; margin-bottom: 22px; }
  #dir.on { display: block; }
  .dhead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
  .dhead h2 { font-family: var(--disp); font-size: 15px; font-weight: 600; margin-right: 6px; }
  .dhead small { color: var(--faint); font-size: 11.5px; margin-left: auto; }
  .dgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
  .co { background: var(--card); border: 1px solid var(--border); border-radius: 14px;
    padding: 12px 14px; display: flex; flex-direction: column; gap: 9px; }
  .co.self { border-color: rgba(255,120,90,.45); }
  .co.pick { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
  .co .cn { display: flex; align-items: center; gap: 8px; }
  .co .cn b { font-size: 14px; font-weight: 600; cursor: pointer; }
  .co .cn b:hover { color: var(--accent); }
  .co .cn .pc { margin-left: auto; font-size: 11px; color: var(--faint); font-variant-numeric: tabular-nums; }
  .links { display: flex; flex-wrap: wrap; gap: 6px; }
  .pl { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; text-decoration: none;
    border: 1px solid var(--border); border-radius: 8px; padding: 4.5px 9px; color: var(--muted);
    background: #0c0f16; transition: border-color .18s, color .18s; white-space: nowrap; }
  .pl .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  a.pl:hover { color: var(--text); border-color: var(--border-hi); }
  .pl.none { color: var(--faint); border-style: dashed; opacity: .7; }
  .pl.web .dot { background: #8a94a6; }
  .pl.fb .dot { background: #4e8cff; }
  .pl.xhs .dot { background: #ff4d6d; }
  .pl.ig .dot { background: linear-gradient(135deg, #f9b233, #e1306c 55%, #8a3ab9); }
  .btn.sync { border-color: rgba(63,214,143,.45); color: var(--green); }
  .btn.sync:hover { background: rgba(63,214,143,.08); }
  .btn.sync.busy { opacity: .75; cursor: progress; }
  #syncBox { display: none; max-width: 1440px; margin: 12px auto 0; background: var(--card);
    border: 1px solid rgba(63,214,143,.35); border-radius: 12px; padding: 12px 14px;
    font-size: 12.5px; line-height: 1.7; color: var(--muted); }
  #syncBox.on { display: block; }
  #syncBox b { color: var(--text); }
  #syncBox a { color: var(--accent); }
  #syncBox ol { margin: 4px 0 8px 20px; }
  #syncBox .srow2 { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 8px; }
  #syncBox input { flex: 1; min-width: 220px; background: #0c0f16; color: var(--text);
    border: 1px solid var(--border); border-radius: 9px; padding: 7px 10px; font-size: 12.5px; font-family: var(--font); }
  #syncBox input:focus { border-color: var(--accent); outline: none; }
  #syncBox .warn { color: var(--orange); }
  #syncBox .ok { color: var(--green); }
  .pdot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .pdot.fb { background: #4e8cff; }
  .pdot.xhs { background: #ff4d6d; }
  .pdot.ig { background: linear-gradient(135deg, #f9b233, #e1306c 55%, #8a3ab9); }
  .embed a.xthumb { position: absolute; inset: 0; display: block; background: #0b0e15; text-decoration: none; }
  .embed a.xthumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .embed a.xthumb span { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%);
    background: rgba(255,77,109,.92); color: #fff; font-size: 12.5px; font-weight: 600;
    padding: 7px 14px; border-radius: 99px; white-space: nowrap; }
  .cotag { font-size: 10.5px; color: var(--muted); border: 1px solid var(--border);
    border-radius: 99px; padding: 2px 8px; white-space: nowrap; }

  footer { color: var(--faint); font-size: 11px; text-align: center; padding: 22px; }

  #toast { position: fixed; bottom: 26px; left: 50%; transform: translateX(-50%) translateY(8px);
    background: var(--card-hi); border: 1px solid var(--border-hi); border-radius: 11px;
    padding: 11px 20px; font-size: 13px; display: none; z-index: 99; opacity: 0;
    box-shadow: 0 12px 32px rgba(0,0,0,.5); transition: opacity .2s, transform .2s; }
  #toast.show { display: block; opacity: 1; transform: translateX(-50%) translateY(0); }

  @media (prefers-reduced-motion: reduce) {
    * { animation: none !important; transition: none !important; }
  }
</style>
</head>
<body>
<svg style="display:none" xmlns="http://www.w3.org/2000/svg">
  <symbol id="i-flame" viewBox="0 0 24 24"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></symbol>
  <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></symbol>
  <symbol id="i-heart" viewBox="0 0 24 24"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.29 1.51 4.04 3 5.5l7 7Z"/></symbol>
  <symbol id="i-msg" viewBox="0 0 24 24"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/></symbol>
  <symbol id="i-users" viewBox="0 0 24 24"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></symbol>
  <symbol id="i-trend" viewBox="0 0 24 24"><path d="M22 7l-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/></symbol>
  <symbol id="i-zap" viewBox="0 0 24 24"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></symbol>
  <symbol id="i-star" viewBox="0 0 24 24"><path d="M12 2l2.9 6.9 7.1.6-5.4 4.7 1.6 7-6.2-3.7-6.2 3.7 1.6-7L2 9.5l7.1-.6z"/></symbol>
  <symbol id="i-magnet" viewBox="0 0 24 24"><path d="m6 15-4-4 6.75-6.77a7.79 7.79 0 0 1 11 11L13 22l-4-4 6.39-6.36a2.14 2.14 0 0 0-3-3L6 15"/><path d="m5 8 4 4"/><path d="m12 15 4 4"/></symbol>
  <symbol id="i-copy" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></symbol>
  <symbol id="i-clip" viewBox="0 0 24 24"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M9 12h6"/><path d="M9 16h6"/></symbol>
  <symbol id="i-ext" viewBox="0 0 24 24"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></symbol>
  <symbol id="i-key" viewBox="0 0 24 24"><circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3L22 7l-3-3"/></symbol>
  <symbol id="i-video" viewBox="0 0 24 24"><path d="m10 8 6 4-6 4Z"/><rect x="2" y="4" width="20" height="16" rx="3"/></symbol>
  <symbol id="i-image" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></symbol>
  <symbol id="i-layers" viewBox="0 0 24 24"><path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/></symbol>
  <symbol id="i-grip" viewBox="0 0 24 24"><circle cx="9" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="19" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="19" r="1"/></symbol>
  <symbol id="i-pct" viewBox="0 0 24 24"><path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/></symbol>
  <symbol id="i-mic" viewBox="0 0 24 24"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/></symbol>
</svg>
<header>
  <div class="hrow">
    <div class="brand">
      <div class="mark"><svg class="ic"><use href="#i-flame"/></svg></div>
      <h1>__BRAND__<small id="updated"></small></h1>
    </div>
    <div class="search"><svg class="ic"><use href="#i-search"/></svg>
      <input id="fSearch" placeholder="搜文案 / hashtag / 账号"></div>
    <select id="fTracker"><option value="">全部 Tracker</option></select>
    <select id="fPlat"><option value="">全部 Social Media</option></select>
    <select id="fComp"><option value="">全部公司</option></select>
    <select id="fSort">
      <option value="x">按 爆款倍数 (vs 自家平均)</option>
      <option value="score">按 Viral Score</option>
      <option value="er">按 Engagement Rate</option>
      <option value="comments">按 Comments</option>
      <option value="likes">按 Likes</option>
      <option value="date">按 最新日期</option>
    </select>
    <button class="btn" id="fVideo"><svg class="ic"><use href="#i-video"/></svg>只看视频</button>
    <button class="btn" id="fDir"><svg class="ic"><use href="#i-users"/></svg>竞对名录</button>
    <button class="btn sync" id="fSync" title="马上抓最新资料,不用等每周排程"><svg class="ic"><use href="#i-zap"/></svg><span id="fSyncTxt">Sync Now</span></button>

    <a class="btn" href="team.html" target="_top" title="工作台"><svg class="ic"><use href="#i-users"/></svg>工作台</a>
  </div>
  <div id="syncBox"></div>
  <div class="tabs" id="tabs"></div>
  <div class="chips" id="presets"></div>
  <div class="chips" id="trends"></div>
</header>
<main>
  <section id="dir">
    <div class="dhead"><h2>竞对名录</h2><span id="platChips" class="chips" style="margin:0"></span>
      <small>点公司名 = 只看该公司的爆款 · 帖子从 Instagram、Facebook、小红书抓</small></div>
    <div class="dgrid" id="dgrid"></div>
  </section>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none"><svg class="ic"><use href="#i-search"/></svg><span id="emptyMsg">没有符合条件的帖子</span></div>
  <div class="more" id="moreWrap" style="display:none"><button id="moreBtn">载入更多</button></div>
</main>
<footer>数据来源 Supabase · __SYNC_DAY__自动更新 · 状态更改即时同步全团队 · Instagram / Facebook 帖子由官方 embed 播放,小红书点封面打开</footer>
<div id="toast"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const SUPA = '__SUPA_URL__';
const ANON = '__SUPA_ANON__';
const STATUSES = __STATUSES__;
document.getElementById('updated').textContent = '更新于 __UPDATED__';
const PAGE = 48;
let shown = PAGE;
let videoOnly = false;
let curTab = '全部';

const $ = id => document.getElementById(id);
const fmt = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n);
const getKey = () => ANON;  // anon key is baked in — safe, can only change status
const I = n => '<svg class="ic"><use href="#i-' + n + '"/></svg>';
const esc = s => String(s).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const byRec = {}; DATA.forEach(d => byRec[d.id] = d);
const TRENDS = __TRENDS__;
DATA.forEach(d => {
  d.days = d.date ? Math.max(0, Math.floor((Date.now() - new Date(d.date + 'T00:00:00')) / 864e5)) : null;
  d.horse = d.followers > 0 && d.followers < 50000 && ((d.x || 0) >= 2 || d.score >= 10);
});
const PRESETS = [
  { k: 'px', icon: 'trend', label: '真爆款 ×2+', f: d => (d.x || 0) >= 2 },
  { k: 'pn', icon: 'star', label: '近7天', f: d => d.days !== null && d.days <= 7 },
  { k: 'ph', icon: 'zap', label: '黑马', f: d => d.horse },
  { k: 'pb', icon: 'magnet', label: '引流帖', f: d => d.bait },
];
const presetOn = {};

function toast(msg, ok) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  t.style.borderColor = ok ? 'var(--green)' : 'var(--red)';
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2600);
}

// companies (competitors.json): map each post's IG handle to its company
const COMPANIES = __COMPANIES__;
const IG2CO = {};
COMPANIES.forEach(c => { if (c.instagram) IG2CO[c.instagram.toLowerCase()] = c.name; });
// every scraped post comes from Instagram today; d.plat keeps the filter honest
// if another platform's scraper is added later
DATA.forEach(d => {
  d.plat = d.plat || 'instagram';
  // the generator matches FB / 小红书 accounts; older pages only had IG
  if (!d.co && d.plat === 'instagram') d.co = IG2CO[(d.competitor || '').toLowerCase()] || '';
  d.co = d.co || '';
  if (!d.who) { d.who = '@' + d.competitor; d.whoUrl = 'https://www.instagram.com/' + d.competitor + '/'; }
});
const PLATS = [
  { k: 'website', label: '官网', cls: 'web' },
  { k: 'facebook', label: 'Facebook', cls: 'fb' },
  { k: 'xiaohongshu', label: '小红书', cls: 'xhs' },
  { k: 'instagram', label: 'Instagram', cls: 'ig' },
];
let curPlat = '';
const PLABEL = {}, PCLS = {};
PLATS.forEach(p => { PLABEL[p.k] = p.label; PCLS[p.k] = p.cls; });

// filters
const coCount = {}; DATA.forEach(d => { const k = d.co || d.who; coCount[k] = (coCount[k] || 0) + 1; });
COMPANIES.forEach(c => {
  const o = document.createElement('option'); o.value = c.name;
  o.textContent = c.name + ' · ' + (coCount[c.name] || 0);
  $('fComp').appendChild(o);
});
const others = [...new Set(DATA.filter(d => !d.co).map(d => d.who))].sort();
if (others.length) {
  const g = document.createElement('optgroup'); g.label = '其他账号';
  others.forEach(h => { const o = document.createElement('option'); o.value = h; o.textContent = h + ' · ' + coCount[h]; g.appendChild(o); });
  $('fComp').appendChild(g);
}
PLATS.filter(p => p.k !== 'website' && p.k !== 'instagram').forEach(p => {
  const o = document.createElement('option'); o.value = p.k;
  o.textContent = p.label + ' · ' + DATA.filter(d => d.plat === p.k).length + ' 帖';
  $('fPlat').appendChild(o);
});
[...new Set(DATA.map(d => d.tracker))].forEach(t => { const o = document.createElement('option'); o.value = t; o.textContent = t; $('fTracker').appendChild(o); });

function matchQ(d) {
  const q = ($('fSearch').value || '').trim().toLowerCase();
  return !q || (d.caption + ' ' + (d.tags || '') + ' ' + (d.transcript || '') + ' ' + (d.brk || '') + ' ' + d.competitor + ' ' + d.co).toLowerCase().includes(q);
}

function compOf(d) { return d.co || d.who; }

function basePool() {
  const t = $('fTracker').value, c = $('fComp').value, pf = $('fPlat').value;
  return DATA.filter(d => (!t || d.tracker === t) && (!c || compOf(d) === c) && (!pf || d.plat === pf) && (!videoOnly || d.video)
    && matchQ(d) && PRESETS.every(p => !presetOn[p.k] || p.f(d)));
}

function filtered() {
  const s = $('fSort').value;
  let arr = basePool().filter(d => curTab === '全部' || d.status === curTab);
  const key = { score:'score', er:'er', comments:'comments', likes:'likes', x:'x' }[s];
  if (s === 'date') arr.sort((a,b) => (b.date||'').localeCompare(a.date||''));
  else arr.sort((a,b) => (b[key]||0) - (a[key]||0));
  return arr;
}

let dragRec = null;

function renderTabs() {
  const pool = basePool();
  const counts = { '全部': pool.length };
  STATUSES.forEach(s => counts[s] = pool.filter(d => d.status === s).length);
  $('tabs').innerHTML = '';
  ['全部', ...STATUSES].forEach(name => {
    const el = document.createElement('div');
    el.className = 'tab' + (curTab === name ? ' on' : '') + (name !== '全部' ? ' droppable' : '');
    el.innerHTML = esc(name) + ' <span class="cnt">' + counts[name] + '</span>';
    el.onclick = () => { curTab = name; shown = PAGE; render(); };
    if (name !== '全部') {
      el.ondragover = e => { if (dragRec) { e.preventDefault(); el.classList.add('over'); } };
      el.ondragleave = () => el.classList.remove('over');
      el.ondrop = e => {
        e.preventDefault(); el.classList.remove('over');
        const d = byRec[dragRec]; dragRec = null;
        if (d && d.status !== name) setStatus(d, name);
      };
    }
    $('tabs').appendChild(el);
  });
}

const io = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      const el = e.target, pid = el.dataset.pid, d = byRec[pid] || {};
      if (!el.querySelector('iframe, a.xthumb')) {
        if (d.plat === 'xiaohongshu') {
          // 小红书 has no embed player: show the cover and open the note on click
          const a = document.createElement('a');
          a.className = 'xthumb'; a.href = d.url; a.target = '_blank'; a.rel = 'noopener';
          a.innerHTML = (d.thumb ? '<img loading="lazy" referrerpolicy="no-referrer" alt="">' : '')
            + '<span>' + (d.video ? '▶ ' : '') + '在小红书打开</span>';
          if (d.thumb) a.querySelector('img').src = d.thumb;
          el.appendChild(a);
        } else {
          const f = document.createElement('iframe');
          f.src = d.plat === 'facebook'
            ? 'https://www.facebook.com/plugins/' + (d.video ? 'video' : 'post') + '.php?href='
              + encodeURIComponent(d.url) + '&show_text=true&width=500'
            : 'https://www.instagram.com/p/' + pid + '/embed/';
          f.loading = 'lazy'; f.allow = 'autoplay; encrypted-media; clipboard-write';
          el.appendChild(f);
        }
      }
      io.unobserve(el);
    }
  });
}, { rootMargin: '400px' });

async function setStatus(d, val) {
  try {
    const r = await fetch(SUPA + '/rest/v1/posts?post_id=eq.' + encodeURIComponent(d.id), {
      method: 'PATCH',
      headers: { 'apikey': ANON, 'Content-Type': 'application/json', 'Prefer': 'return=minimal' },
      body: JSON.stringify({ status: val })
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d.status = val;
    cacheStatus(d.id, val);
    toast('✓ ' + d.who + ' 已移到「' + val + '」', true);
  } catch (e) {
    toast('更新失败: ' + e.message + (String(e.message).includes('401') || String(e.message).includes('403') ? ' — 密钥无效?' : ''), false);
  }
  render();
}

function card(d, idx) {
  const el = document.createElement('div');
  el.className = 'card' + ((d.x || 0) >= 2 ? ' outlier' : '');
  el.style.animationDelay = Math.min(idx % PAGE, 14) * 30 + 'ms';
  el.draggable = true;
  el.ondragstart = e => {
    dragRec = d.id;
    e.dataTransfer.effectAllowed = 'move';
    el.classList.add('lifting');
    document.body.classList.add('dragging');
  };
  el.ondragend = () => {
    el.classList.remove('lifting');
    document.body.classList.remove('dragging');
    dragRec = null;
  };
  const typeIcon = d.video ? 'video' : (d.type === 'Carousel' ? 'layers' : 'image');
  const days = d.days, horse = d.horse;
  const ago = days === null ? '' : (days === 0 ? '今天' : days + '天前');
  const lines = (d.caption || '').split('\\n').map(l => l.trim()).filter(Boolean);
  const hook = lines[0] || '';
  const tr = (d.transcript && d.transcript !== '(转录失败)' && d.transcript !== '(无口播内容)') ? d.transcript : '';
  el.innerHTML = `
    <div class="embed" data-pid="${d.id}"><div class="ph"><svg class="ic"><use href="#i-${typeIcon}"/></svg></div></div>
    <div class="meta">
      <div class="row1">
        <span class="handle" title="拖到上方 tab 即可归类">${I('grip')}</span>
        <span class="pdot ${PCLS[d.plat] || 'ig'}" title="${PLABEL[d.plat] || 'Instagram'}"></span>
        <a class="who" href="${esc(d.whoUrl)}" target="_blank" rel="noopener">${esc(d.who)}</a>
        ${d.co ? '<span class="cotag">' + esc(d.co) + '</span>' : ''}
        <span class="badge">${d.type || '-'}</span>
        ${days !== null && days <= 7 ? '<span class="badge bnew">' + I('star') + '新帖</span>' : ''}
        ${horse ? '<span class="badge bhorse">' + I('zap') + '黑马</span>' : ''}
        ${d.bait ? '<span class="badge bbait" title="「留言XX」引流打法 — 评论被引流放大,漏斗设计值得学">' + I('magnet') + '引流</span>' : ''}
        <span class="date" title="${d.date || ''}">${ago}</span>
      </div>
      <div class="nums">
        <span class="num hot" title="Viral Score">${I('flame')}<b>${d.score}</b></span>
        ${(d.x || 0) >= 2 ? '<span class="num x" title="是自家平均热度的多少倍">' + I('trend') + '<b>×' + d.x + '</b></span>' : ''}
        <span class="num er" title="Engagement Rate">${I('pct')}<b>${d.er}%</b></span>
        <span class="num" title="Likes">${I('heart')}<b>${d.likes > 0 ? fmt(d.likes) : '隐藏'}</b></span>
        <span class="num" title="Comments">${I('msg')}<b>${fmt(d.comments)}</b></span>
        <span class="num" title="Followers">${I('users')}<b>${fmt(d.followers)}</b></span>
      </div>
      <div class="cap" onclick="this.classList.toggle('open')"></div>
      ${d.brk ? '<div class="ai" onclick="this.classList.toggle(&quot;open&quot;)"></div>' : ''}
      ${d.my ? '<div class="mine" onclick="this.classList.toggle(&quot;open&quot;)"></div>' : ''}
      <div class="srow"><label>状态</label><select class="status btn s${d.status}"></select>
        <button class="abtn copy" title="复制完整文案">${I('copy')}文案</button>
        ${tr ? '<button class="abtn mic" title="复制视频口播逐字稿">' + I('mic') + '口播</button>' : ''}
        ${d.my ? '<button class="abtn minec" title="复制我的改写口播稿(可直接拍)">' + I('mic') + '我的稿</button>' : ''}
        <button class="abtn brief" title="复制拍摄 Brief(数据+Hook+AI拆解+我的稿+口播稿+文案)">${I('clip')}Brief</button>
        <a class="abtn" href="${esc(d.url)}" target="_blank" rel="noopener" title="打开 ${PLABEL[d.plat] || 'Instagram'} 原帖">${I('ext')}</a></div>
    </div>`;
  const capEl = el.querySelector('.cap');
  const restTxt = lines.slice(1).join('\\n');
  capEl.innerHTML = hook ? '<b>' + esc(hook) + '</b>' + (restTxt ? '<br>' + esc(restTxt).replace(/\\n/g, '<br>') : '') : '';
  const aiEl = el.querySelector('.ai');
  if (aiEl) aiEl.innerHTML = '<b>💡 AI 拆解</b><br>' + esc(d.brk).split(String.fromCharCode(10)).join('<br>');
  const myEl = el.querySelector('.mine');
  if (myEl) myEl.innerHTML = '<b>🎬 我的版本(可直接拍)</b><br>' + esc(d.my).split(String.fromCharCode(10)).join('<br>');
  const sel = el.querySelector('.status');
  STATUSES.forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o); });
  sel.value = d.status;
  sel.onchange = () => setStatus(d, sel.value);
  el.querySelector('.copy').onclick = () =>
    navigator.clipboard.writeText(d.caption || '').then(() => toast('✓ 文案已复制', true));
  const micBtn = el.querySelector('.mic');
  if (micBtn) micBtn.onclick = () =>
    navigator.clipboard.writeText(tr).then(() => toast('✓ 口播稿已复制', true));
  const mineBtn = el.querySelector('.minec');
  if (mineBtn) mineBtn.onclick = () =>
    navigator.clipboard.writeText(d.my).then(() => toast('✓ 我的改写稿已复制', true));
  el.querySelector('.brief').onclick = () => {
    const NL = String.fromCharCode(10);
    const parts = ['🎬 爆款参考 ' + (PLABEL[d.plat] || 'Instagram') + ' ' + d.who + (d.co ? '(' + d.co + ')' : '') + ((d.x || 0) >= 2 ? '(自家平均 ×' + d.x + ')' : ''),
      '📊 🔥' + d.score + ' | ER ' + d.er + '% | ❤️ ' + (d.likes > 0 ? fmt(d.likes) : '隐藏') +
      ' | 💬 ' + fmt(d.comments) + ' | 👥 ' + fmt(d.followers) + (ago ? ' | ' + ago : ''),
      '🔗 ' + d.url, '✍️ Hook: ' + hook];
    if (d.brk) parts.push('——— AI 拆解 ———', d.brk);
    if (d.my) parts.push('——— 我的版本(可直接拍)———', d.my);
    if (tr) parts.push('——— 竞对原口播稿 ———', tr);
    parts.push('——— 完整文案 ———', d.caption || '');
    navigator.clipboard.writeText(parts.join(NL)).then(() => toast('✓ Brief 已复制,可直接发给拍摄/剪辑', true));
  };
  io.observe(el.querySelector('.embed'));
  return el;
}

function plLink(c, p) {
  const v = c[p.k];
  if (!v) return '<span class="pl ' + p.cls + ' none"><span class="dot"></span>' + p.label + ' ' + (p.k === 'instagram' ? '未找到' : '未提供') + '</span>';
  let href = v, txt = p.label;
  if (p.k === 'instagram') { href = 'https://www.instagram.com/' + v + '/'; txt = '@' + v; }
  else if (p.k === 'website') { try { txt = new URL(v).hostname.replace(/^www\\./, ''); } catch (e) {} }
  return '<a class="pl ' + p.cls + '" href="' + esc(href) + '" target="_blank" rel="noopener"><span class="dot"></span>' + esc(txt) + '</a>';
}

function renderPlatChips() {
  const box = $('platChips'); box.innerHTML = '';
  [{ k: '', label: '全部平台' }, ...PLATS].forEach(p => {
    const n = p.k ? COMPANIES.filter(c => c[p.k]).length : COMPANIES.length;
    const el = document.createElement('span');
    el.className = 'chip2' + (curPlat === p.k ? ' on' : '');
    el.textContent = p.label + ' ' + n + (p.k ? '/' + COMPANIES.length : '');
    el.onclick = () => {
      curPlat = p.k;
      // keep the header dropdown in step (it has no 官网 option)
      $('fPlat').value = [...$('fPlat').options].some(o => o.value === p.k) ? p.k : '';
      shown = PAGE; render();
    };
    box.appendChild(el);
  });
}

function renderDir() {
  renderPlatChips();
  const cur = $('fComp').value;
  const plats = curPlat ? PLATS.filter(p => p.k === curPlat) : PLATS;
  const list = COMPANIES.slice();
  if (curPlat) list.sort((a, b) => (b[curPlat] ? 1 : 0) - (a[curPlat] ? 1 : 0));
  const g = $('dgrid'); g.innerHTML = '';
  list.forEach(c => {
    const el = document.createElement('div');
    el.className = 'co' + (c.self ? ' self' : '') + (cur === c.name ? ' pick' : '');
    const n = coCount[c.name] || 0;
    el.innerHTML = '<div class="cn"><b title="只看这家公司的爆款">' + esc(c.name) + '</b>'
      + '<span class="pc">' + (n ? n + ' 条爆款' : '还没抓到帖子') + '</span></div>'
      + '<div class="links">' + plats.map(p => plLink(c, p)).join('') + '</div>';
    el.querySelector('b').onclick = () => {
      $('fComp').value = cur === c.name ? '' : c.name; shown = PAGE; render();
      if ($('fComp').value) $('grid').scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    g.appendChild(el);
  });
}

function render() {
  renderTabs();
  if ($('dir').classList.contains('on')) renderDir();
  const arr = filtered();
  const grid = $('grid'); grid.innerHTML = '';
  arr.slice(0, shown).forEach((d, i) => grid.appendChild(card(d, i)));
  $('empty').style.display = arr.length ? 'none' : '';
  const pf = $('fPlat').value, pl = PLATS.find(p => p.k === pf);
  $('emptyMsg').textContent = pl && !DATA.some(d => d.plat === pf)
    ? '还没有 ' + pl.label + ' 的帖子:先在 Supabase 加上各公司的 ' + pl.label + ' 账号,等每周同步。上面「竞对名录」列出各公司的 ' + pl.label + ' 主页。'
    : '没有符合条件的帖子';
  $('moreWrap').style.display = arr.length > shown ? '' : 'none';
}


function applyStatusCache() {
  try {
    const c = JSON.parse(localStorage.getItem('st_cache') || '{}');
    Object.keys(c).forEach(rid => { const d = byRec[rid]; if (d) d.status = c[rid]; });
  } catch (e) {}
}

function cacheStatus(rid, val) {
  try {
    const c = JSON.parse(localStorage.getItem('st_cache') || '{}');
    c[rid] = val; localStorage.setItem('st_cache', JSON.stringify(c));
  } catch (e) {}
}

// live status refresh (so weekly-baked page shows current statuses)
async function refreshStatuses() {
  applyStatusCache(); render();  // last-known statuses first
  if (Date.now() - (+localStorage.getItem('st_ts') || 0) < 600000) return;  // 10-min throttle
  localStorage.setItem('st_ts', String(Date.now()));
  const fresh = {};
  try {
    const r = await fetch(SUPA + '/rest/v1/posts?select=post_id,status', { headers: { 'apikey': ANON } });
    if (!r.ok) return;
    (await r.json()).forEach(row => {
      const d = byRec[row.post_id];
      if (d) d.status = row.status || '未处理';
      if (row.status && row.status !== '未处理') fresh[row.post_id] = row.status;
    });
    localStorage.setItem('st_cache', JSON.stringify(fresh));
    render();
  } catch (e) {}
}

['fTracker','fComp','fSort'].forEach(id => $(id).onchange = () => { shown = PAGE; render(); });
$('fPlat').onchange = () => {
  curPlat = $('fPlat').value;
  // a platform with no scraped posts yet: open the directory so its pages are one click away
  if (curPlat && !DATA.some(d => d.plat === curPlat) && !$('dir').classList.contains('on')) {
    $('dir').classList.add('on'); $('fDir').classList.add('on');
  }
  shown = PAGE; render();
};
$('fSearch').oninput = () => { shown = PAGE; render(); };
$('fVideo').onclick = () => { videoOnly = !videoOnly; $('fVideo').classList.toggle('on', videoOnly); shown = PAGE; render(); };
$('moreBtn').onclick = () => { shown += PAGE; render(); };
$('fDir').onclick = () => {
  const on = !$('dir').classList.contains('on');
  $('dir').classList.toggle('on', on); $('fDir').classList.toggle('on', on);
  try { localStorage.setItem('dir_on', on ? '1' : ''); } catch (e) {}
  if (on) renderDir();
};
try { if (localStorage.getItem('dir_on')) { $('dir').classList.add('on'); $('fDir').classList.add('on'); } } catch (e) {}

// preset quick filters
$('presets').innerHTML = '<span class="lbl">快筛</span>';
PRESETS.forEach(p => {
  const c = document.createElement('span'); c.className = 'chip2';
  c.innerHTML = I(p.icon) + esc(p.label);
  c.onclick = () => { presetOn[p.k] = !presetOn[p.k]; c.classList.toggle('on', presetOn[p.k]); shown = PAGE; render(); };
  $('presets').appendChild(c);
});

// trending hashtags among outlier posts
if (TRENDS.length) {
  $('trends').innerHTML = '<span class="lbl">爆款热词</span>';
  TRENDS.forEach(([tag, n]) => {
    const c = document.createElement('span'); c.className = 'chip2';
    c.textContent = '#' + tag + ' ' + n;
    c.onclick = () => { $('fSearch').value = $('fSearch').value === tag ? '' : tag; shown = PAGE; render(); };
    $('trends').appendChild(c);
  });
} else { $('trends').style.display = 'none'; }

// ---- Sync Now: start the Sync Content workflow from the page ----
// A GitHub fine-grained token (Actions: read & write, this repo only) is
// pasted once and kept in this browser's localStorage. It is never baked
// into the page, so the public URL alone cannot start paid Apify runs.
const GH = { owner: location.hostname.split('.')[0],
             repo: location.pathname.split('/').filter(Boolean)[0] || '' };
const GH_API = 'https://api.github.com/repos/' + GH.owner + '/' + GH.repo + '/actions';
const GH_WEB = 'https://github.com/' + GH.owner + '/' + GH.repo + '/actions/workflows/sync.yml';
const CHAIN = ['Sync Content', 'Transcribe Videos', 'Analyze Posts', 'Deploy Dashboard'];
const lsGet = k => { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } };
const lsSet = (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) {} };
let syncTimer = null;

function syncBox(html) { const b = $('syncBox'); b.innerHTML = html; b.classList.toggle('on', !!html); }
function syncBtn(txt, busy) { $('fSyncTxt').textContent = txt; $('fSync').classList.toggle('busy', !!busy); }

async function gh(path, opts) {
  const r = await fetch(GH_API + path, Object.assign({ headers: {
    'Accept': 'application/vnd.github+json', 'Authorization': 'Bearer ' + lsGet('gh_tok') } }, opts || {}));
  if (r.status === 401 || r.status === 403 || r.status === 404) { const e = new Error('token'); e.code = r.status; throw e; }
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.status === 204 ? null : r.json();
}

function showSetup(msg) {
  syncBox((msg ? '<div class="warn">' + esc(msg) + '</div>' : '')
    + '<b>第一次用 Sync Now,要先放一把 GitHub 钥匙(只存在这台电脑的浏览器)</b>'
    + '<ol><li>打开 <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">GitHub → 新建 fine-grained token</a></li>'
    + '<li>Repository access 选 <b>Only select repositories</b> → <b>' + esc(GH.repo) + '</b></li>'
    + '<li>Permissions → Repository permissions → <b>Actions: Read and write</b>,其他不用开</li>'
    + '<li>按 Generate token,复制 <b>github_pat_</b> 开头那串,贴在下面</li></ol>'
    + '<div class="srow2"><input id="ghTok" type="password" placeholder="github_pat_..." autocomplete="off">'
    + '<button class="btn" id="ghSave">保存并同步</button><button class="btn" id="ghClose">取消</button></div>'
    + '<div style="margin-top:6px">不想放钥匙?也可以 <a href="' + GH_WEB + '" target="_blank" rel="noopener">到 GitHub 手动按 Run workflow</a>。'
    + '每同步一次约用 US$2 的 Apify 额度。</div>');
  $('ghSave').onclick = () => {
    const v = ($('ghTok').value || '').trim();
    if (!v) return;
    lsSet('gh_tok', v); syncNow(true);
  };
  $('ghClose').onclick = () => syncBox('');
}

async function chainRunning() {
  const d = await gh('/runs?per_page=15');
  return (d.workflow_runs || []).find(r => CHAIN.includes(r.name) && r.status !== 'completed');
}

async function syncNow(confirmed) {
  if (!lsGet('gh_tok')) return showSetup();
  syncBtn('检查中…', true);
  try {
    const running = await chainRunning();
    if (running) { syncBtn('同步中…', true); watchSync(running.created_at); return; }
    if (!confirmed) {
      syncBtn('Sync Now');
      syncBox('<b>马上同步最新资料?</b> 会抓 Instagram、Facebook、小红书,再转录、AI 拆解、更新网页,'
        + '大约 20–60 分钟。每次约用 US$2 的 Apify 额度。'
        + '<div class="srow2"><button class="btn sync" id="syncGo">开始同步</button>'
        + '<button class="btn" id="syncNo">取消</button>'
        + '<button class="btn" id="syncForget" title="清除这台电脑保存的 GitHub 钥匙">清除钥匙</button></div>');
      $('syncGo').onclick = () => syncNow(true);
      $('syncNo').onclick = () => syncBox('');
      $('syncForget').onclick = () => { lsSet('gh_tok', ''); syncBox(''); toast('已清除这台电脑的 GitHub 钥匙', true); };
      return;
    }
    const since = new Date(Date.now() - 5000).toISOString();
    await gh('/workflows/sync.yml/dispatches', { method: 'POST', body: JSON.stringify({ ref: 'main' }) });
    lsSet('sync_since', since);
    watchSync(since);
  } catch (e) {
    syncBtn('Sync Now');
    if (e.message === 'token') { lsSet('gh_tok', ''); showSetup('钥匙无效或权限不够(HTTP ' + e.code + '),请重新建一把。'); }
    else syncBox('<span class="warn">同步没开始:' + esc(e.message) + '</span> 可以 <a href="' + GH_WEB + '" target="_blank" rel="noopener">到 GitHub 手动按 Run workflow</a>。');
  }
}

// follow the chain until a Deploy Dashboard run that started after `since` finishes
function watchSync(since) {
  clearTimeout(syncTimer);
  syncBtn('同步中…', true);
  const tick = async () => {
    try {
      const runs = ((await gh('/runs?per_page=20')).workflow_runs || [])
        .filter(r => CHAIN.includes(r.name) && r.created_at >= since)
        .sort((a, b) => a.created_at.localeCompare(b.created_at));
      const bad = runs.find(r => r.status === 'completed' && !['success', 'skipped'].includes(r.conclusion));
      if (bad) {
        syncBtn('Sync Now'); lsSet('sync_since', '');
        syncBox('<span class="warn">「' + esc(bad.name) + '」失败了。</span> <a href="' + bad.html_url + '" target="_blank" rel="noopener">打开日志看原因</a>');
        return;
      }
      const done = runs.find(r => r.name === 'Deploy Dashboard' && r.status === 'completed');
      if (done) {
        syncBtn('Sync Now'); lsSet('sync_since', '');
        syncBox('<span class="ok">✓ 同步完成,网页已更新。</span> <a href="#" id="syncReload">按这里重新整理</a>(GitHub Pages 可能要再等 1 分钟)');
        $('syncReload').onclick = e => { e.preventDefault(); location.reload(); };
        return;
      }
      const cur = runs.length ? runs[runs.length - 1] : null;
      const step = cur ? CHAIN.indexOf(cur.name) + 1 : 0;
      syncBox('同步中:' + (cur ? '第 ' + step + '/4 步「' + esc(cur.name) + '」' : '等 GitHub 开始…')
        + '。可以关掉这页,跑完网页会自动更新。 <a href="' + GH_WEB.replace('/workflows/sync.yml', '') + '" target="_blank" rel="noopener">在 GitHub 看进度</a>');
    } catch (e) {
      if (e.message === 'token') { syncBtn('Sync Now'); showSetup('钥匙失效了,请重新建一把。'); return; }
    }
    syncTimer = setTimeout(tick, 30000);
  };
  tick();
}

$('fSync').onclick = () => { if (!$('fSync').classList.contains('busy')) syncNow(false); };
// a sync started from this browser earlier: keep showing its progress
if (lsGet('gh_tok') && lsGet('sync_since')) watchSync(lsGet('sync_since'));

render();
refreshStatuses();
</script>
</body>
</html>
"""


TEAM_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>工作台</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#0a0d13; --card:#11151d; --card-hi:#151a24; --border:#1d2430; --border-hi:#2c3546;
    --text:#e8ecf3; --muted:#8a94a6; --faint:#5b6577; --accent:#5ea1ff; --green:#3fd68f;
    --orange:#f0a33f; --pink:#ff6b9d; --red:#ff5c5c; --fire1:#ff8a3d; --fire2:#ff4d6d;
    --font:'Inter',-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
    --disp:'Space Grotesk','Inter','PingFang SC','Microsoft YaHei',sans-serif;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:var(--font); -webkit-font-smoothing:antialiased; }
  ::selection { background:rgba(94,161,255,.3); }
  ::-webkit-scrollbar { width:10px; } ::-webkit-scrollbar-thumb { background:#232c3b; border-radius:8px; }
  :focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:6px; }
  .ic { width:14px; height:14px; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; fill:none; flex:none; }

  header { position:sticky; top:0; z-index:50; background:rgba(10,13,19,.86);
    backdrop-filter:blur(14px); border-bottom:1px solid var(--border); padding:13px 20px; }
  .hrow { display:flex; flex-wrap:wrap; gap:10px; align-items:center; max-width:1200px; margin:0 auto; }
  .brand { display:flex; align-items:center; gap:10px; margin-right:auto; }
  .mark { width:31px; height:31px; border-radius:9px; display:grid; place-items:center;
    background:linear-gradient(135deg,var(--fire1),var(--fire2)); box-shadow:0 4px 16px rgba(255,92,80,.35); }
  .mark .ic { width:17px; height:17px; color:#fff; }
  h1 { font-family:var(--disp); font-size:16.5px; font-weight:700; line-height:1.15; }
  h1 small { display:block; color:var(--faint); font-family:var(--font); font-weight:400; font-size:10.5px; }
  .me { display:flex; align-items:center; gap:8px; font-size:13px; color:var(--muted); cursor:pointer;
    background:var(--card); border:1px solid var(--border); border-radius:10px; padding:7px 12px;
    transition:border-color .18s; }
  .me:hover { border-color:var(--border-hi); }
  .me b { color:var(--text); }

  nav { display:flex; gap:7px; max-width:1200px; margin:12px auto 0; flex-wrap:wrap; }
  .ntab { display:inline-flex; align-items:center; gap:7px; background:var(--card);
    border:1px solid var(--border); border-radius:999px; padding:7px 16px; font-size:13.5px;
    color:var(--muted); cursor:pointer; user-select:none; transition:all .18s; }
  .ntab:hover { border-color:var(--border-hi); color:var(--text); }
  .ntab.on { color:var(--text); background:linear-gradient(135deg,rgba(255,138,61,.15),rgba(255,77,109,.15));
    border-color:rgba(255,120,90,.45); }

  main { max-width:1200px; margin:22px auto; padding:0 20px; }
  .page { display:none; } .page.on { display:block; }

  .stats { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }
  .stat { flex:1; min-width:130px; background:var(--card); border:1px solid var(--border);
    border-radius:14px; padding:14px 16px; }
  .stat .v { font-family:var(--disp); font-size:26px; font-weight:700; font-variant-numeric:tabular-nums; }
  .stat .l { color:var(--faint); font-size:12px; margin-top:2px; }
  .stat.sh .v { color:var(--orange); } .stat.dn .v { color:var(--green); } .stat.pd .v { color:var(--accent); }

  section { background:var(--card); border:1px solid var(--border); border-radius:16px;
    padding:16px 18px; margin-bottom:16px; }
  section h2 { font-size:14px; font-weight:600; display:flex; align-items:center; gap:8px; margin-bottom:12px; }
  section h2 .ic { color:var(--orange); }
  section h2 small { color:var(--faint); font-weight:400; font-size:11.5px; margin-left:auto; }
  .row { display:flex; align-items:center; gap:10px; padding:10px 0; border-top:1px solid var(--border);
    font-size:13px; flex-wrap:wrap; }
  .row .hookline { flex:1; min-width:200px; color:var(--text); overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap; }
  .row .hookline small { color:var(--faint); display:block; font-size:11px; margin-top:1px; }
  .row .who2 { color:var(--accent); text-decoration:none; font-weight:600; font-size:12px; white-space:nowrap; }
  .xb { font-family:var(--disp); font-weight:600; font-size:11px; color:var(--orange);
    border:1px solid rgba(240,163,63,.4); background:rgba(240,163,63,.07); border-radius:99px;
    padding:2px 8px; white-space:nowrap; }
  .pill { display:inline-flex; align-items:center; gap:4px; background:#0c0f16; color:var(--muted);
    border:1px solid var(--border); border-radius:9px; padding:5.5px 10px; font-size:11.5px;
    cursor:pointer; text-decoration:none; font-family:var(--font); transition:all .18s; white-space:nowrap; }
  .pill .ic { width:11px; height:11px; }
  .pill:hover { color:var(--accent); border-color:rgba(94,161,255,.5); }
  .pill.ok:hover { color:var(--green); border-color:rgba(63,214,143,.5); }
  .pill.bad:hover { color:var(--red); border-color:rgba(255,92,92,.5); }
  .none { color:var(--faint); font-size:13px; padding:14px 0 6px; }

  .act { display:flex; gap:9px; padding:7px 0; border-top:1px solid var(--border);
    font-size:12.5px; color:var(--muted); }
  .act time { color:var(--faint); font-variant-numeric:tabular-nums; white-space:nowrap; }
  .act b { color:var(--text); font-weight:600; }
  .act .to { color:var(--orange); }

  #radarWrap { height:calc(100vh - 150px); min-height:500px; border:1px solid var(--border);
    border-radius:16px; overflow:hidden; }
  #radarWrap iframe { width:100%; height:100%; border:0; }

  .sop { max-width:820px; }
  .sop h3 { font-size:15px; margin:22px 0 10px; display:flex; gap:8px; align-items:center; }
  .sop h3 .ic { color:var(--orange); }
  .sop ol { margin:0 0 8px 22px; color:var(--muted); font-size:13.5px; line-height:1.9; }
  .sop ol b { color:var(--text); }
  .sop p { color:var(--muted); font-size:13px; line-height:1.7; margin-bottom:6px; }
  .sop table { border-collapse:collapse; font-size:13px; margin:8px 0; }
  .sop td, .sop th { border:1px solid var(--border); padding:7px 14px; color:var(--muted); }
  .sop th { color:var(--text); background:var(--card-hi); }

  #mission { border:1px solid rgba(255,120,90,.35); border-radius:16px; padding:15px 18px;
    margin-bottom:14px; background:linear-gradient(135deg,rgba(255,138,61,.10),rgba(255,77,109,.10)); }
  #mission .mt { font-weight:600; font-size:14px; display:flex; gap:8px; align-items:center; }
  #mission .mt .ic { color:var(--orange); }
  #mission .md { color:var(--muted); font-size:12.5px; margin-top:5px; line-height:1.6; }
  #mission .md b { color:var(--text); font-family:var(--disp); }
  #overlay { position:fixed; inset:0; background:rgba(6,8,12,.88); backdrop-filter:blur(6px);
    z-index:200; display:none; place-items:center; }
  #overlay.show { display:grid; }
  .obox { background:var(--card); border:1px solid var(--border-hi); border-radius:18px;
    padding:28px 30px; width:min(92vw,380px); }
  .obox h2 { font-family:var(--disp); font-size:18px; margin-bottom:6px; }
  .obox p { color:var(--muted); font-size:13px; margin-bottom:16px; }
  .obox input, .obox select { width:100%; background:#0c0f16; color:var(--text);
    border:1px solid var(--border); border-radius:10px; padding:10px 13px; font-size:14px;
    font-family:var(--font); margin-bottom:11px; }
  .obox input:focus, .obox select:focus { border-color:var(--accent); outline:none; }
  .obox button { width:100%; background:linear-gradient(135deg,var(--fire1),var(--fire2));
    color:#fff; border:0; border-radius:10px; padding:11px; font-size:14px; font-weight:600;
    font-family:var(--font); cursor:pointer; }
  .obox button:active { transform:scale(.98); }

  #toast { position:fixed; bottom:26px; left:50%; transform:translateX(-50%) translateY(8px);
    background:var(--card-hi); border:1px solid var(--border-hi); border-radius:11px;
    padding:11px 20px; font-size:13px; display:none; z-index:300; opacity:0;
    box-shadow:0 12px 32px rgba(0,0,0,.5); transition:opacity .2s, transform .2s; }
  #toast.show { display:block; opacity:1; transform:translateX(-50%) translateY(0); }
  @media (prefers-reduced-motion: reduce) { * { animation:none!important; transition:none!important; } }
</style>
</head>
<body>
<svg style="display:none" xmlns="http://www.w3.org/2000/svg">
  <symbol id="i-flame" viewBox="0 0 24 24"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></symbol>
  <symbol id="i-user" viewBox="0 0 24 24"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></symbol>
  <symbol id="i-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></symbol>
  <symbol id="i-x" viewBox="0 0 24 24"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></symbol>
  <symbol id="i-ext" viewBox="0 0 24 24"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></symbol>
  <symbol id="i-video" viewBox="0 0 24 24"><path d="m10 8 6 4-6 4Z"/><rect x="2" y="4" width="20" height="16" rx="3"/></symbol>
  <symbol id="i-book" viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></symbol>
  <symbol id="i-radar" viewBox="0 0 24 24"><path d="M19.07 4.93A10 10 0 0 0 6.99 3.34"/><path d="M4 6h.01"/><path d="M2.29 9.62A10 10 0 1 0 21.31 8.35"/><path d="M16.24 7.76A6 6 0 1 0 8.23 16.67"/><path d="M12 18h.01"/><path d="M17.99 11.66A6 6 0 0 1 15.77 16.67"/><circle cx="12" cy="12" r="2"/><path d="m13.41 10.59 5.66-5.66"/></symbol>
  <symbol id="i-arrow" viewBox="0 0 24 24"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></symbol>
</svg>
<header>
  <div class="hrow">
    <div class="brand">
      <div class="mark"><svg class="ic"><use href="#i-flame"/></svg></div>
      <h1>工作台<small id="updated"></small></h1>
    </div>
    <div class="me" id="meBtn" title="点击修改身份">
      <svg class="ic"><use href="#i-user"/></svg><span id="meLabel">未登记</span>
    </div>
  </div>
  <nav>
    <div class="ntab on" data-p="work"><svg class="ic"><use href="#i-clock"/></svg>今日工作</div>
    <div class="ntab" data-p="radar"><svg class="ic"><use href="#i-radar"/></svg>爆款雷达</div>
    <div class="ntab" data-p="sop"><svg class="ic"><use href="#i-book"/></svg>SOP</div>
  </nav>
</header>
<main>
  <div class="page on" id="p-work">
    <div id="mission"></div>
    <div class="stats">
      <div class="stat pd"><div class="v" id="stPending">–</div><div class="l">未处理(待挑选)</div></div>
      <div class="stat sh"><div class="v" id="stShoot">–</div><div class="l">拍摄中</div></div>
      <div class="stat dn"><div class="v" id="stDone">–</div><div class="l">已处理</div></div>
    </div>
    <section id="secShoot">
      <h2><svg class="ic"><use href="#i-video"/></svg>拍摄中队列<small>做完一条,点「完成」</small></h2>
      <div id="shootList"></div>
    </section>
    <section id="secPick">
      <h2><svg class="ic"><use href="#i-flame"/></svg>本周待挑选爆款 Top 8<small>看中就点「选中拍摄」,细看去爆款雷达</small></h2>
      <div id="pickList"></div>
    </section>
  </div>
  <div class="page" id="p-radar"><div id="radarWrap"></div></div>
  <div class="page sop" id="p-sop">
    <section>
      <h3><svg class="ic"><use href="#i-flame"/></svg>SOP-01 · Marketer 每周选题流程(周一,约30分钟)</h3>
      <ol>
        <li>打开<b>爆款雷达</b>,点快筛「<b>真爆款 ×2+</b>」+「<b>近7天</b>」</li>
        <li>看「爆款热词」确认本周主题方向(热词越多的主题越优先)</li>
        <li>挑 <b>3–5 条</b>适合改编的。标准:话题跟我们相关 / Hook 可复制 / 我们拍得出来</li>
        <li>每条点「<b>Brief</b>」复制 → 发到拍摄群</li>
        <li>把选中的卡片<b>拖到「拍摄中」</b></li>
        <li>明显不适合的拖「跳过」,保持未处理越少越好</li>
      </ol>
    </section>
    <section>
      <h3><svg class="ic"><use href="#i-video"/></svg>SOP-02 · Content Creator 拍摄改编流程(每条 ≤1天)</h3>
      <ol>
        <li>打开工作台「<b>今日工作</b>」,看拍摄中队列,从最旧的开始</li>
        <li>打开原帖看 <b>3 遍</b>:第1遍看整体 / 第2遍拆 Hook(前3秒为什么让人停下) / 第3遍记结构(几个镜头、什么节奏)</li>
        <li><b>改编不照抄</b>:换成我们的案例、口吻、行业。保留结构,替换内容</li>
        <li>拍摄 → 剪辑。<b>前3秒必须有钩子</b>,字幕开头即出</li>
        <li>发布后回到工作台,该条点「<b>完成</b>」</li>
      </ol>
    </section>
    <section>
      <h3><svg class="ic"><use href="#i-book"/></svg>SOP-03 · 状态定义(团队统一语言)</h3>
      <table>
        <tr><th>状态</th><th>意思</th><th>谁负责改</th></tr>
        <tr><td>未处理</td><td>还没人筛选过</td><td>Marketer 每周清理</td></tr>
        <tr><td>拍摄中</td><td>已选中,正在改编/拍摄/剪辑</td><td>Marketer 选中时拖入</td></tr>
        <tr><td>已处理</td><td>已发布或已完成改编</td><td>Creator 完成时点</td></tr>
        <tr><td>跳过</td><td>不适合我们,不再考虑</td><td>任何人</td></tr>
      </table>
    </section>
    <section>
      <h3><svg class="ic"><use href="#i-radar"/></svg>SOP-04 · 看到「引流」标签的帖子怎么学</h3>
      <p>带「引流」标的帖子,重点<b>不是内容,是漏斗</b>。记录三件事:</p>
      <ol>
        <li>留言<b>关键词</b>是什么?(例:留言「Start」)</li>
        <li>送的<b>诱饵</b>是什么?(模板 / 课程 / 清单)</li>
        <li>之后导去<b>哪里</b>?(DM 自动回复 → 链接 → 报名页)</li>
      </ol>
      <p>好的打法直接抄结构,用我们的 GHL / ManyChat 实现。</p>
    </section>
    <section>
      <h3><svg class="ic"><use href="#i-user"/></svg>SOP-05 · 竞对名单管理(发现好账号就加进来)</h3>
      <ol>
        <li>打开 Supabase「<b>competitors</b>」表,加一行</li>
        <li><b>Username</b>:贴账号名或 IG <b>主页</b>链接都行(带 ?igsh= 参数没关系;不要贴帖子链接)</li>
        <li><b>Tracker</b>:选 IG Competitors 或 AI Competitors</li>
        <li><b>Active</b>:打勾✅ — <b>没勾不会抓</b></li>
        <li>每周一自动生效;停止追踪 = 取消勾,不用删行</li>
      </ol>
      <p>加账号的标准:跟我们领域相关 / 内容有爆款相 / 值得学。发现雷达里某账号长期没爆款,把它 Active 取消,腾出额度换新的。</p>
    </section>
    <section>
      <h3><svg class="ic"><use href="#i-user"/></svg>SOP-06 · 第一次设置 & 常见问题</h3>
      <ol>
        <li><b>登记身份:</b>第一次打开工作台会弹「你是谁」→ 输名字、选角色。这决定你看到的任务卡和排版</li>
        <li><b>直接改状态:</b>雷达里把卡片拖到上方分类,或用卡片下拉选。看到绿色 ✓ 就成了。<b>不需要输任何密钥</b> —— 这套系统天生只允许改状态,改不了别的数据,所以安全无需登录</li>
      </ol>
      <p><b>常见问题:</b></p>
      <table>
        <tr><th>现象</th><th>原因</th><th>解决</th></tr>
        <tr><td>改了状态没反应 / 报错</td><td>网络断了,或页面是旧版</td><td>刷新页面(Ctrl+Shift+R)再试</td></tr>
        <tr><td>队友改的状态我看不到</td><td>页面最多 10 分钟同步一次</td><td>刷新页面即可;自己的改动是即时的</td></tr>
        <tr><td>网页打不开 / 数据是空的</td><td>每周自动更新那天可能还在跑</td><td>等 1 小时后再开;还不行找老板看 GitHub Actions</td></tr>
      </table>
      <p>状态存在云端(Supabase),换电脑、关机都不会丢,全团队实时共享。</p>
    </section>
  </div>
</main>
<div id="overlay"><div class="obox">
  <h2>👋 你是谁?</h2>
  <p>登记一次,方便团队知道是谁在操作。</p>
  <input id="oName" placeholder="你的名字" maxlength="20">
  <select id="oRole">
    <option value="Marketer">Marketer(选题/投放)</option>
    <option value="Content Creator">Content Creator(拍摄/剪辑)</option>
    <option value="Boss">Boss</option>
  </select>
  <button id="oSave">进入工作台</button>
</div></div>
<div id="toast"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const SUPA = '__SUPA_URL__';
const ANON = '__SUPA_ANON__';
document.getElementById('updated').textContent = '数据更新于 __UPDATED__';

const $ = id => document.getElementById(id);
const I = n => '<svg class="ic"><use href="#i-' + n + '"/></svg>';
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n);
const getKey = () => ANON;  // anon key is baked in — safe, can only change status
const firstLine = c => ((c || '').split('\\n').map(l => l.trim()).filter(Boolean)[0] || '(无文案)');
const byRec = {}; DATA.forEach(d => byRec[d.id] = d);

function toast(msg, ok) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  t.style.borderColor = ok ? 'var(--green)' : 'var(--red)';
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2600);
}

// ---- mission card ----
function renderMission() {
  const role = localStorage.getItem('tm_role') || '';
  const name = localStorage.getItem('tm_name') || '';
  const pend = DATA.filter(d => d.status === '未处理').length;
  const hot = DATA.filter(d => d.status === '未处理' && (d.x || 0) >= 2).length;
  const queue = DATA.filter(d => d.status === '拍摄中').length;
  const done = DATA.filter(d => d.status === '已处理').length;
  const m = $('mission');
  if (role === 'Marketer') {
    m.innerHTML = '<div class="mt">' + I('flame') + esc(name) + ',任务:从待挑选里选出值得拍的</div>'
      + '<div class="md">待挑选 <b>' + pend + '</b> 条(其中真爆款 ×2+ 有 <b>' + hot + '</b> 条)'
      + ' · 从下面清单直接选,或去雷达细看</div>';
  } else if (role === 'Content Creator') {
    m.innerHTML = '<div class="mt">' + I('video') + esc(name) + ',任务:清空拍摄中队列</div>'
      + '<div class="md">队列还有 <b>' + queue + '</b> 条待做 · 已完成共 <b>' + done + '</b> 条</div>';
  } else {
    m.innerHTML = '<div class="mt">' + I('flame') + '团队概览</div>'
      + '<div class="md">待挑选 <b>' + pend + '</b> 条 · 拍摄中 <b>' + queue + '</b> 条 · 已完成 <b>' + done + '</b> 条</div>';
  }
}

function orderSections() {
  // Marketer sees picks first; Creator sees shooting queue first
  if ((localStorage.getItem('tm_role') || '') === 'Marketer') {
    $('secShoot').parentNode.insertBefore($('secPick'), $('secShoot'));
  } else {
    $('secPick').parentNode.insertBefore($('secShoot'), $('secPick'));
  }
}

// ---- identity ----
function meLabel() {
  const n = localStorage.getItem('tm_name'), r = localStorage.getItem('tm_role');
  $('meLabel').innerHTML = n ? '<b>' + esc(n) + '</b>&nbsp;· ' + esc(r || '') : '未登记';
}
function showOverlay() {
  $('oName').value = localStorage.getItem('tm_name') || '';
  $('oRole').value = localStorage.getItem('tm_role') || 'Marketer';
  $('overlay').classList.add('show');
}
$('oSave').onclick = () => {
  const n = $('oName').value.trim();
  if (!n) { $('oName').focus(); return; }
  localStorage.setItem('tm_name', n);
  localStorage.setItem('tm_role', $('oRole').value);
  $('overlay').classList.remove('show');
  meLabel(); orderSections(); renderMission();
  refreshStatuses();
};
$('meBtn').onclick = showOverlay;
meLabel();
if (!localStorage.getItem('tm_name')) showOverlay();

// ---- tabs ----
document.querySelectorAll('.ntab').forEach(t => t.onclick = () => {
  document.querySelectorAll('.ntab').forEach(x => x.classList.toggle('on', x === t));
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('on', p.id === 'p-' + t.dataset.p));
  if (t.dataset.p === 'radar' && !$('radarWrap').querySelector('iframe')) {
    const f = document.createElement('iframe'); f.src = 'index.html'; $('radarWrap').appendChild(f);
  }
});

// ---- status write ----
async function setStatus(d, val) {
  try {
    const r = await fetch(SUPA + '/rest/v1/posts?post_id=eq.' + encodeURIComponent(d.id), {
      method: 'PATCH',
      headers: { 'apikey': ANON, 'Content-Type': 'application/json', 'Prefer': 'return=minimal' },
      body: JSON.stringify({ status: val })
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d.status = val;
    cacheStatus(d.id, val);
    toast('✓ ' + (d.who || ('@' + d.competitor)) + ' → ' + val, true);
  } catch (e) {
    toast('失败: ' + e.message, false);
  }
  renderWork();
}

// ---- 今日工作 ----
function pillBtn(cls, icon, label) {
  return '<button class="pill ' + cls + '">' + I(icon) + label + '</button>';
}

function renderWork() {
  const pend = DATA.filter(d => d.status === '未处理');
  const shoot = DATA.filter(d => d.status === '拍摄中');
  const done = DATA.filter(d => d.status === '已处理');
  $('stPending').textContent = pend.length;
  $('stShoot').textContent = shoot.length;
  $('stDone').textContent = done.length;

  // shooting queue
  const sl = $('shootList'); sl.innerHTML = '';
  if (!shoot.length) sl.innerHTML = '<div class="none">队列是空的 — Marketer 去爆款雷达挑几条拖进「拍摄中」</div>';
  shoot.forEach(d => {
    const r = document.createElement('div'); r.className = 'row';
    r.innerHTML = '<a class="who2" href="' + esc(d.whoUrl || ('https://www.instagram.com/' + d.competitor + '/')) + '" target="_blank" rel="noopener">' + esc(d.who || ('@' + d.competitor)) + '</a>'
      + ((d.x || 0) >= 2 ? '<span class="xb">×' + d.x + '</span>' : '')
      + '<span class="hookline">' + esc(firstLine(d.caption)) + '</span>'
      + '<a class="pill" href="' + d.url + '" target="_blank">' + I('ext') + '原帖</a>'
      + pillBtn('ok', 'check', '完成') + pillBtn('bad', 'x', '退回');
    const btns = r.querySelectorAll('button');
    btns[0].onclick = () => setStatus(d, '已处理');
    btns[1].onclick = () => setStatus(d, '未处理');
    sl.appendChild(r);
  });

  // picks
  const picks = pend.filter(d => (d.x || 0) >= 2).sort((a, b) => (b.x || 0) - (a.x || 0)).slice(0, 8);
  const pl = $('pickList'); pl.innerHTML = '';
  if (!picks.length) pl.innerHTML = '<div class="none">暂时没有 ×2 以上的未处理爆款 — 等周一自动抓新数据</div>';
  picks.forEach(d => {
    const r = document.createElement('div'); r.className = 'row';
    r.innerHTML = '<span class="xb">×' + d.x + '</span>'
      + '<a class="who2" href="' + esc(d.whoUrl || ('https://www.instagram.com/' + d.competitor + '/')) + '" target="_blank" rel="noopener">' + esc(d.who || ('@' + d.competitor)) + '</a>'
      + '<span class="hookline">' + esc(firstLine(d.caption))
      + '<small>🔥' + d.score + ' · ER ' + d.er + '% · 💬' + fmt(d.comments) + '</small></span>'
      + '<a class="pill" href="' + d.url + '" target="_blank">' + I('ext') + '原帖</a>'
      + pillBtn('ok', 'video', '选中拍摄') + pillBtn('bad', 'x', '跳过');
    const btns = r.querySelectorAll('button');
    btns[0].onclick = () => setStatus(d, '拍摄中');
    btns[1].onclick = () => setStatus(d, '跳过');
    pl.appendChild(r);
  });
  renderMission();
}

function applyStatusCache() {
  try {
    const c = JSON.parse(localStorage.getItem('st_cache') || '{}');
    Object.keys(c).forEach(rid => { const d = byRec[rid]; if (d) d.status = c[rid]; });
  } catch (e) {}
}

function cacheStatus(rid, val) {
  try {
    const c = JSON.parse(localStorage.getItem('st_cache') || '{}');
    c[rid] = val; localStorage.setItem('st_cache', JSON.stringify(c));
  } catch (e) {}
}

// ---- live statuses from Supabase ----
async function refreshStatuses() {
  applyStatusCache(); renderWork();  // last-known statuses first
  if (Date.now() - (+localStorage.getItem('st_ts') || 0) < 600000) return;  // 10-min throttle
  localStorage.setItem('st_ts', String(Date.now()));
  const fresh = {};
  try {
    const r = await fetch(SUPA + '/rest/v1/posts?select=post_id,status', { headers: { 'apikey': ANON } });
    if (!r.ok) return;
    (await r.json()).forEach(row => {
      const d = byRec[row.post_id];
      if (d) d.status = row.status || '未处理';
      if (row.status && row.status !== '未处理') fresh[row.post_id] = row.status;
    });
    localStorage.setItem('st_cache', JSON.stringify(fresh));
    renderWork();
  } catch (e) {}
}

orderSections();
renderMission();
renderWork();
refreshStatuses();
</script>
</body>
</html>
"""


def main():
    svc = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or SUPABASE_ANON_KEY or "").strip()
    if not svc:
        print("ERROR: SUPABASE_SERVICE_ROLE_KEY is not set.")
        sys.exit(1)
    if not SUPABASE_ANON_KEY:
        print("ERROR: SUPABASE_ANON_KEY is not set (needed for browser reads/writes).")
        sys.exit(1)
    if not SUPABASE_URL:
        print("ERROR: SUPABASE_URL is not set.")
        sys.exit(1)

    print("Fetching posts from Supabase...")
    rows = fetch_posts(svc)
    print(f"  {len(rows)} rows")
    posts = [normalize(r) for r in rows]
    posts = [p for p in posts if p]

    # de-dup by post id (keep first)
    seen, unique = set(), []
    for p in posts:
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)

    # Only accounts still ticked `active` belong on the board. Unticking one in
    # Supabase hides it here; its rows stay in the table, so ticking it back
    # later brings the whole history with it.
    active = fetch_active_handles(svc)
    if active is not None:
        kept = [p for p in unique if _handle(p["competitor"]) in active]
        dropped = sorted({p["competitor"] for p in unique
                          if _handle(p["competitor"]) not in active})
        if kept:
            if dropped:
                print("  hidden (active not ticked): " + ", ".join(dropped))
            print(f"  {len(unique)} -> {len(kept)} posts after the active filter")
            unique = kept
        elif unique:
            print("  WARNING: that would hide every post - skipping the active "
                  "filter. Check the usernames in the competitors table.")

    # company name per post, matched on the platform's own account key
    co_by = {}
    for c in load_companies():
        for plat in ("instagram", "facebook", "xiaohongshu"):
            k = _handle(c[plat]) if c[plat] else ""
            if k:
                co_by[(plat, k)] = c["name"]
    for p in unique:
        p["co"] = co_by.get((p["plat"], _handle(p["competitor"])), "")
        if p["co"] and p["plat"] == "xiaohongshu":
            p["who"] = "小红书主页"  # the company tag beside it names the account

    # outlier multiplier: post score vs the account's own median score
    import statistics
    by_comp = {}
    # keyed by platform too: the same handle on IG and FB is two audiences
    for p in unique:
        by_comp.setdefault((p["plat"], p["competitor"]), []).append(p["score"] or 0)
    for p in unique:
        scores = [s for s in by_comp[(p["plat"], p["competitor"])] if s > 0]
        med = statistics.median(scores) if len(scores) >= 3 else 0
        p["x"] = round((p["score"] or 0) / med, 1) if med > 0 else 0

    # trending hashtags among outlier posts
    from collections import Counter
    cnt = Counter()
    for p in unique:
        if (p.get("x") or 0) >= 2 or (p["score"] or 0) >= 10:
            for tag in re.split(r"[,\s]+", p.get("tags") or ""):
                tag = tag.strip().lstrip("#").lower()
                if len(tag) >= 2:
                    cnt[tag] += 1
    trends = [[t, n] for t, n in cnt.most_common(15) if n >= 2][:10]

    myt = timezone(timedelta(hours=8))
    updated = datetime.now(myt).strftime("%Y-%m-%d %H:%M") + " (GMT+8)"
    payload = json.dumps(unique, ensure_ascii=False).replace("</", "<\\/")
    html = (
        HTML_TEMPLATE
        .replace("__BRAND__", BRAND)
        .replace("__SYNC_DAY__", SYNC_DAY)
        .replace("__DATA__", payload)
        .replace("__UPDATED__", updated)
        .replace("__SUPA_URL__", SUPABASE_URL)
        .replace("__SUPA_ANON__", SUPABASE_ANON_KEY)
        .replace("__STATUSES__", json.dumps(STATUSES, ensure_ascii=False))
        .replace("__TRENDS__", json.dumps(trends, ensure_ascii=False))
        .replace("__COMPANIES__", json.dumps(load_companies(), ensure_ascii=False).replace("</", "<\\/"))
    )

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nWrote {OUT_PATH} with {len(unique)} posts.")

    # team portal — slim payload (no captions beyond first 200 chars needed for hooks)
    team_html = (
        TEAM_TEMPLATE
        .replace("__DATA__", payload)
        .replace("__UPDATED__", updated)
        .replace("__SUPA_URL__", SUPABASE_URL)
        .replace("__SUPA_ANON__", SUPABASE_ANON_KEY)
    )
    team_path = os.path.join(os.path.dirname(OUT_PATH), "team.html")
    with open(team_path, "w", encoding="utf-8") as f:
        f.write(team_html)
    print(f"Wrote {team_path}")


if __name__ == "__main__":
    main()
