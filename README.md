# ✱ Field Notes

A [RAPP Vision](https://kody-w.github.io/rapp-vision/) channel.

> Post-mortems from autonomous systems that looked perfectly healthy while being completely frozen.

**Channel:** <https://kody-w.github.io/rappvision-field-notes/>
**Add to any player:** `https://kody-w.github.io/rappvision-field-notes/rappvision/channel.json`

---

## What's here

| Path | What it is |
|---|---|
| `rappvision/channel.json` | The only file you edit. `rapp-vision-channel/1.0`. |
| `rappvision/media/` | `.mp4` + `.webm` per video |
| `rappvision/thumbs/` | poster image per video |
| `build.py` | regenerates everything below from `channel.json` |
| `index.html` | channel landing page |
| `v/<id>.html` | per-video **share page** |
| `e/<id>.html` | bare embed (iframes, Twitter player cards) |
| `feed.xml` | RSS 2.0 with `<enclosure>` |

`index.html`, `v/`, `e/` and `feed.xml` are **generated**. Don't hand-edit them — the next
build overwrites your change. `python3 build.py --check` fails if any of them is stale, so
the drift is caught rather than discovered later.

## Add a video

```bash
cp yourvideo.mp4  rappvision/media/my-video.mp4
cp yourvideo.webm rappvision/media/my-video.webm     # optional, but ~3x smaller
cp poster.jpg     rappvision/thumbs/my-video.jpg
# add one entry to the "videos" array in rappvision/channel.json
python3 build.py
```

That's the whole workflow. One JSON entry gets a share page, an embed page, an RSS item
and a card on the channel page.

## Why the share pages exist

RAPP Vision's player routes with a hash (`#/watch/<id>`). Hash fragments are never sent to
a server, so **every** video link unfurls identically on X, LinkedIn, Slack and Discord —
the generic player, no title, no thumbnail. That's fine for one video and useless for fifty.

So each video also gets a real URL at `v/<id>.html` carrying:

- per-video Open Graph tags (`og:video`, `og:image`, title, description)
- a **Twitter player card** pointing at `e/<id>.html`, so the post plays inline
- **JSON-LD `VideoObject`** so search engines can index it as video
- a working `<video>` with both sources, so the shared link is watchable on its own —
  no app shell, no JavaScript framework, no network round-trip to a registry
- chapter deep links (`v/<id>.html#t=95` starts 95 seconds in)
- a Share button using `navigator.share`, falling back to clipboard

The canonical place to watch is still the network player; these pages just make a link
worth pasting.

## Paths are relative on purpose

Every `src` in `channel.json` resolves **against that file's own URL**, per the
`rapp-vision-channel/1.0` spec. Mirror this repo, rename it, serve it from anywhere — the
media still resolves. The generated pages need absolute URLs (Open Graph requires them),
so `build.py` holds a single `SITE` constant. That's the one line to change in a fork.

## Media scaling

Videos are committed directly, which is right at this size (~20 MB per video, both formats).
Past roughly 40–50 videos this repo would get uncomfortable to clone, and the fix is to move
`rappvision/media/` to GitHub Release assets and point `sources[].src` at those URLs — the
spec allows absolute URLs, and nothing else in the channel has to change.

Worth stating plainly, because the first video on this channel is about a repo that
died from exactly this: a tracked file crossed GitHub's 100 MB per-file limit and the
pre-receive hook then rejected **every** push, taking six workflows down with it.

## Licence

Code in this repo (`build.py`, page templates): MIT.
Video content: © the author, all rights reserved.


## YouTube export

Turn any video in this channel into an upload-ready bundle:

```bash
python3 export-youtube.py --list                          # what can export, and to what
python3 export-youtube.py above-not-beside                # 16:9, as published
python3 export-youtube.py above-not-beside --format square # 1:1
python3 export-youtube.py above-not-beside --format shorts # 9:16
```

Each bundle contains the video, a thumbnail, `title.txt`, `tags.txt`, and a
`description.txt` with the chapter timestamps already formatted for YouTube.

**Two kinds of video export differently.** Script-based videos are built from a
HyperFrames composition and can be retargeted to any ratio. Burned-in
recordings — a live RAPP Vision session captured to video — have their framing
baked into the pixels, so reformatting one could only crop, which would cut off
captions and lower thirds. The exporter refuses rather than doing that quietly.

**Retargeting fits and pads; it never crops.** Rewriting a composition's root
`data-width`/`data-height` and re-rendering looks like the right answer and is
not: the layout is still the 1920-wide one, simply cut off at the edge. The
render succeeds and `ffprobe` reports the dimensions you asked for, so the
output is quietly broken. Retargets are verified against `ffprobe` after the
fact rather than trusted to a zero exit code.

The exporter also checks your chapters against YouTube's rules — start at 0:00,
at least 3, each 10s or longer — because breaking any of them makes YouTube
silently render no chapters at all.
