#!/usr/bin/env python3
"""build.py — regenerate the channel's sharing layer from rappvision/channel.json.

RAPP Vision's player routes with a hash (`#/watch/<id>`). Hash fragments are never
sent to a server, so a link pasted into X, LinkedIn, Slack or Discord unfurls as the
generic player, identically for every video. That is the one thing that does not
scale as a channel grows.

So each video also gets real, server-visible URLs:

    v/<id>.html   share landing  — per-video OG + Twitter player card + JSON-LD,
                                   and a working <video> so the shared link is
                                   itself watchable with no app shell
    e/<id>.html   bare embed     — what Twitter/iframe embeds actually load
    index.html    channel page   — every video, newest first
    feed.xml      RSS 2.0        — <enclosure> per video, so podcast/video readers
                                   can subscribe to the channel

Adding a video is one entry in channel.json plus `python3 build.py`.
Python 3 stdlib only — no build step, no dependencies, matching the rest of the network.

Usage:  python3 build.py [--check]
        --check  regenerate into memory and fail if anything on disk is stale (CI guard)
"""

import html
import json
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHANNEL_PATH = ROOT / "rappvision" / "channel.json"

# Absolute origin, needed because OG/Twitter/JSON-LD require absolute URLs.
SITE = "https://kody-w.github.io/rappvision-field-notes"
# Where the channel is playable on the network player.
NETWORK = "https://kody-w.github.io/rapp-vision/"

BRAND_BG = "#faf9f5"
BRAND_INK = "#141413"
BRAND_ACCENT = "#cc785c"
BRAND_MUTED = "#73726f"


def esc(text):
    return html.escape(str(text), quote=True)


def iso_duration(seconds):
    """Seconds -> ISO-8601 duration, which is what schema.org VideoObject wants."""
    total = int(round(float(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    out = "PT"
    if h:
        out += f"{h}H"
    if m:
        out += f"{m}M"
    if s or (not h and not m):
        out += f"{s}S"
    return out


def clock(seconds):
    total = int(float(seconds))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def media_url(channel_rel_path):
    """channel.json paths resolve against the channel.json URL, per the spec."""
    return f"{SITE}/rappvision/{channel_rel_path}"


def pick_source(video, want):
    for source in video.get("sources", []):
        if want in source.get("type", ""):
            return source
    return None


PAGE_CSS = f"""
  *{{box-sizing:border-box}}
  body{{margin:0;background:{BRAND_BG};color:{BRAND_INK};
    font:16px/1.6 "EB Garamond",Georgia,serif;-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:960px;margin:0 auto;padding:32px 20px 72px}}
  a{{color:{BRAND_ACCENT}}}
  .mono{{font-family:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
    font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:{BRAND_MUTED}}}
  h1{{font-size:34px;line-height:1.2;margin:.4em 0 .2em;font-weight:600}}
  /* the width/height attributes are presentational hints, so height="1080" lands as
     a CSS height and letterboxes the player. height:auto + aspect-ratio undoes that
     while keeping the attributes for crawlers and layout stability. */
  video{{width:100%;height:auto;aspect-ratio:16/9;border-radius:8px;background:#000;
    display:block;border:1px solid rgba(20,20,19,.12)}}
  .desc{{white-space:pre-wrap;margin:20px 0;font-size:17px}}
  .row{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:18px 0}}
  .btn{{display:inline-block;padding:9px 15px;border-radius:999px;text-decoration:none;
    border:1px solid rgba(20,20,19,.2);color:{BRAND_INK};background:#fff;
    font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:12px;cursor:pointer}}
  .btn.pri{{background:{BRAND_ACCENT};border-color:{BRAND_ACCENT};color:#fff}}
  ol.ch{{list-style:none;padding:0;margin:8px 0 0}}
  ol.ch li{{padding:5px 0;border-bottom:1px solid rgba(20,20,19,.08)}}
  ol.ch a{{text-decoration:none;color:{BRAND_INK}}}
  ol.ch .t{{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:12px;
    color:{BRAND_ACCENT};margin-right:12px}}
  .tags span{{display:inline-block;margin:0 6px 6px 0;padding:3px 9px;border-radius:4px;
    background:rgba(20,20,19,.06);font-size:12px;color:{BRAND_MUTED}}}
  .card{{display:block;text-decoration:none;color:inherit;border:1px solid rgba(20,20,19,.12);
    border-radius:10px;overflow:hidden;background:#fff;margin:18px 0}}
  .card img{{width:100%;height:auto;aspect-ratio:16/9;object-fit:cover;display:block}}
  .card .body{{padding:14px 16px}}
  .card h2{{margin:0 0 6px;font-size:21px;font-weight:600;line-height:1.25}}
  footer{{margin-top:44px;padding-top:18px;border-top:1px solid rgba(20,20,19,.12)}}
"""


def share_page(channel, video):
    vid = video["id"]
    title = video["title"]
    desc = video.get("description", "")
    summary = desc.strip().split("\n")[0][:300]
    thumb = media_url(video["thumb"])
    page = f"{SITE}/v/{vid}.html"
    embed = f"{SITE}/e/{vid}.html"
    mp4 = pick_source(video, "mp4")
    mp4_url = media_url(mp4["src"]) if mp4 else ""
    w, h = video.get("width", 1920), video.get("height", 1080)
    watch = f"{NETWORK}#/watch/{vid}"

    sources = "\n".join(
        f'      <source src="{esc(media_url(s["src"]))}" type="{esc(s["type"])}">'
        for s in video.get("sources", [])
    )
    chapters = "\n".join(
        f'      <li><a href="#t={int(c["t"])}" data-t="{c["t"]}">'
        f'<span class="t">{clock(c["t"])}</span>{esc(c["label"])}</a></li>'
        for c in video.get("chapters", [])
    )
    tags = "".join(f"<span>#{esc(t)}</span>" for t in video.get("tags", []))

    ld = {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": title,
        "description": desc,
        "thumbnailUrl": [thumb],
        "uploadDate": video.get("published", ""),
        "duration": iso_duration(video.get("duration", 0)),
        "contentUrl": mp4_url,
        "embedUrl": embed,
        "width": w,
        "height": h,
        "isFamilyFriendly": True,
        "publisher": {"@type": "Organization", "name": channel["name"]},
    }

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — {esc(channel['name'])}</title>
<link rel="canonical" href="{esc(page)}">
<meta name="description" content="{esc(summary)}">

<meta property="og:type" content="video.other">
<meta property="og:site_name" content="{esc(channel['name'])}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(summary)}">
<meta property="og:url" content="{esc(page)}">
<meta property="og:image" content="{esc(thumb)}">
<meta property="og:image:width" content="{w}">
<meta property="og:image:height" content="{h}">
<meta property="og:video" content="{esc(mp4_url)}">
<meta property="og:video:secure_url" content="{esc(mp4_url)}">
<meta property="og:video:type" content="video/mp4">
<meta property="og:video:width" content="{w}">
<meta property="og:video:height" content="{h}">
<meta property="video:duration" content="{int(float(video.get('duration', 0)))}">

<meta name="twitter:card" content="player">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(summary)}">
<meta name="twitter:image" content="{esc(thumb)}">
<meta name="twitter:player" content="{esc(embed)}">
<meta name="twitter:player:width" content="{w}">
<meta name="twitter:player:height" content="{h}">

<meta name="theme-color" content="{BRAND_BG}">
<link rel="alternate" type="application/rss+xml" title="{esc(channel['name'])}" href="{SITE}/feed.xml">
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="mono"><a href="../index.html" style="text-decoration:none">{esc(channel['avatar'])} {esc(channel['name'])}</a></div>
  <h1>{esc(title)}</h1>
  <div class="mono">{esc(video.get('published',''))} &middot; {clock(video.get('duration',0))} &middot; {w}&times;{h}</div>

  <div class="row" style="margin-top:18px">
    <video id="v" controls preload="metadata" playsinline poster="{esc(thumb)}" width="{w}" height="{h}">
{sources}
    </video>
  </div>

  <div class="row">
    <a class="btn pri" href="{esc(watch)}">Watch on RAPP Vision</a>
    <button class="btn" id="share">Share</button>
    <a class="btn" href="{esc(mp4_url)}" download>Download MP4</a>
    <a class="btn" href="{SITE}/feed.xml">RSS</a>
  </div>

  <div class="desc">{esc(desc)}</div>
  <div class="tags">{tags}</div>

  <div class="mono" style="margin-top:26px">Chapters</div>
  <ol class="ch">
{chapters}
  </ol>

  <footer class="mono">
    <a href="../index.html">All videos</a> &middot;
    <a href="{esc(channel['links'][0]['url'])}">Source</a> &middot;
    <a href="{esc(NETWORK)}">RAPP Vision network</a>
  </footer>
</div>
<script>
  var v = document.getElementById('v');
  document.querySelectorAll('ol.ch a').forEach(function (a) {{
    a.addEventListener('click', function (e) {{
      e.preventDefault(); v.currentTime = parseFloat(a.dataset.t); v.play();
    }});
  }});
  // deep link into a chapter: v/<id>.html#t=95
  var m = location.hash.match(/t=(\\d+(?:\\.\\d+)?)/);
  if (m) v.addEventListener('loadedmetadata', function () {{ v.currentTime = parseFloat(m[1]); }});
  document.getElementById('share').addEventListener('click', async function () {{
    var url = {json.dumps(page)}, title = {json.dumps(title)};
    if (navigator.share) {{ try {{ await navigator.share({{title: title, url: url}}); return; }} catch (e) {{}} }}
    try {{ await navigator.clipboard.writeText(url); this.textContent = 'Link copied'; }}
    catch (e) {{ prompt('Copy this link', url); }}
  }});
</script>
</body>
</html>
"""


def embed_page(channel, video):
    """Bare player — what a Twitter player card or an <iframe> actually loads."""
    sources = "\n".join(
        f'  <source src="{esc(media_url(s["src"]))}" type="{esc(s["type"])}">'
        for s in video.get("sources", [])
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(video['title'])}</title>
<style>
  html,body{{margin:0;height:100%;background:#000}}
  video{{width:100%;height:100%;display:block;object-fit:contain}}
</style>
</head>
<body>
<video controls playsinline preload="metadata" poster="{esc(media_url(video['thumb']))}">
{sources}
</video>
</body>
</html>
"""


def index_page(channel, videos):
    cards = ""
    for v in videos:
        summary = v.get("description", "").strip().split("\n")[0]
        cards += f"""  <a class="card" href="v/{esc(v['id'])}.html">
    <img src="rappvision/{esc(v['thumb'])}" alt="" width="{v.get('width',1920)}" height="{v.get('height',1080)}">
    <div class="body">
      <h2>{esc(v['title'])}</h2>
      <div class="mono">{esc(v.get('published',''))} &middot; {clock(v.get('duration',0))}</div>
      <p style="margin:8px 0 0;color:{BRAND_MUTED}">{esc(summary[:220])}</p>
    </div>
  </a>
"""
    links = " &middot; ".join(
        f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>' for l in channel.get("links", [])
    )
    newest_thumb = media_url(videos[0]["thumb"]) if videos else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(channel['name'])} — {esc(channel['tagline'])}</title>
<link rel="canonical" href="{SITE}/">
<meta name="description" content="{esc(channel['tagline'])}">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(channel['name'])}">
<meta property="og:description" content="{esc(channel['tagline'])}">
<meta property="og:url" content="{SITE}/">
<meta property="og:image" content="{esc(newest_thumb)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(channel['name'])}">
<meta name="twitter:description" content="{esc(channel['tagline'])}">
<meta name="twitter:image" content="{esc(newest_thumb)}">
<meta name="theme-color" content="{BRAND_BG}">
<link rel="alternate" type="application/rss+xml" title="{esc(channel['name'])}" href="feed.xml">
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="mono">RAPP Vision channel</div>
  <h1>{esc(channel['avatar'])} {esc(channel['name'])}</h1>
  <p style="font-size:19px;margin:.2em 0 0">{esc(channel['tagline'])}</p>
  <div class="row">
    <a class="btn pri" href="{esc(NETWORK)}#/channel/{esc(channel['id'])}">Open in RAPP Vision</a>
    <a class="btn" href="feed.xml">RSS</a>
    <a class="btn" href="rappvision/channel.json">channel.json</a>
  </div>
  <div class="mono" style="margin-top:8px">{links}</div>

{cards}
  <footer class="mono">
    Add this channel to any RAPP Vision player with
    <code>{SITE}/rappvision/channel.json</code>
  </footer>
</div>
</body>
</html>
"""


def feed_xml(channel, videos):
    # lastBuildDate must be derived from content, never from now(): a clock-based
    # value makes every build differ from the last, which would make `--check`
    # report drift forever and train everyone to ignore it.
    def pub_of(video):
        try:
            return datetime.strptime(video.get("published", ""), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)

    newest = max((pub_of(v) for v in videos), default=datetime(1970, 1, 1, tzinfo=timezone.utc))
    built = format_datetime(newest)
    items = ""
    for v in videos:
        mp4 = pick_source(v, "mp4")
        url = media_url(mp4["src"]) if mp4 else ""
        pub = format_datetime(pub_of(v))
        link = f"{SITE}/v/{v['id']}.html"
        items += f"""  <item>
    <title>{esc(v['title'])}</title>
    <link>{esc(link)}</link>
    <guid isPermaLink="true">{esc(link)}</guid>
    <pubDate>{pub}</pubDate>
    <description>{esc(v.get('description',''))}</description>
    <enclosure url="{esc(url)}" type="video/mp4"/>
    <media:thumbnail url="{esc(media_url(v['thumb']))}"/>
  </item>
"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel>
  <title>{esc(channel['name'])}</title>
  <link>{SITE}/</link>
  <description>{esc(channel['tagline'])}</description>
  <language>en</language>
  <lastBuildDate>{built}</lastBuildDate>
{items}</channel>
</rss>
"""


def build(check_only=False):
    channel = json.loads(CHANNEL_PATH.read_text(encoding="utf-8"))
    videos = sorted(
        channel.get("videos", []), key=lambda v: v.get("published", ""), reverse=True
    )

    outputs = {ROOT / "index.html": index_page(channel, videos),
               ROOT / "feed.xml": feed_xml(channel, videos)}
    for v in videos:
        outputs[ROOT / "v" / f"{v['id']}.html"] = share_page(channel, v)
        outputs[ROOT / "e" / f"{v['id']}.html"] = embed_page(channel, v)

    stale = []
    for path, content in outputs.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != content:
            stale.append(path.relative_to(ROOT))
        if not check_only:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    if check_only:
        if stale:
            print("STALE (run `python3 build.py`): " + ", ".join(str(p) for p in stale))
            return 1
        print(f"up to date — {len(outputs)} generated file(s), {len(videos)} video(s)")
        return 0

    print(f"built {len(outputs)} file(s) for {len(videos)} video(s)")
    for path in sorted(outputs, key=lambda p: str(p)):
        print(f"  {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(build(check_only="--check" in sys.argv))
