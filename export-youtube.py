#!/usr/bin/env python3
"""Export a RAPP Vision video as a YouTube-ready upload bundle.

Two kinds of video live in a channel and they export differently:

  script-based   built from a HyperFrames composition, so the layout can be
                 re-rendered at another aspect ratio. Square and Shorts are
                 real re-renders, not crops.
  burned-in      a recording (a live RAPP Vision session captured to video).
                 The framing is baked into the pixels, so reformatting could
                 only crop — which silently destroys captions and lower
                 thirds. This refuses instead of quietly cropping.

    python3 export-youtube.py above-not-beside
    python3 export-youtube.py above-not-beside --format shorts
    python3 export-youtube.py --all --format square
    python3 export-youtube.py --list

Output lands in export/youtube/<id>--<format>/ with the video, a thumbnail,
and the description already carrying YouTube chapter timestamps.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent
CHANNEL = HOME / "rappvision" / "channel.json"
OUTDIR = HOME / "export" / "youtube"

# YouTube renders chapters only when the list starts at 0:00, has at least
# three entries, and every chapter runs 10s or longer. Violating any of those
# silently produces no chapters at all, which is the kind of quiet failure
# worth catching before upload rather than after.
MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10

FORMATS = {
    "landscape": (1920, 1080, "16:9 — standard YouTube"),
    "square":    (1080, 1080, "1:1 — feed-friendly"),
    "shorts":    (1080, 1920, "9:16 — YouTube Shorts, max 3 minutes"),
}
SHORTS_MAX_SECONDS = 180


def load_channel() -> dict:
    return json.loads(CHANNEL.read_text(encoding="utf-8"))


def timestamp(seconds: float) -> str:
    total = int(seconds)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def check_chapters(chapters: list[dict], duration: float) -> list[str]:
    """Return the reasons YouTube would refuse to render these chapters."""
    problems = []
    if not chapters:
        return ["no chapters defined"]
    if len(chapters) < MIN_CHAPTERS:
        problems.append(f"only {len(chapters)} chapters (YouTube needs {MIN_CHAPTERS})")
    if float(chapters[0]["t"]) != 0.0:
        problems.append("first chapter must start at 0:00")
    bounds = [float(c["t"]) for c in chapters] + [float(duration)]
    for i, c in enumerate(chapters):
        span = bounds[i + 1] - bounds[i]
        if span < MIN_CHAPTER_SECONDS:
            problems.append(
                f'chapter {i + 1} "{c["label"]}" is {span:.0f}s '
                f"(minimum {MIN_CHAPTER_SECONDS}s)")
    return problems


def build_description(video: dict) -> str:
    parts = [video["description"].strip(), ""]
    chapters = video.get("chapters") or []
    if chapters:
        parts += ["Chapters", ""]
        parts += [f'{timestamp(float(c["t"]))} {c["label"]}' for c in chapters]
        parts.append("")
    links = video.get("links") or []
    if links:
        parts += [f'{l["label"]}: {l["url"]}' for l in links]
        parts.append("")
    parts.append("#" + " #".join(t.replace("-", "") for t in video.get("tags", [])))
    return "\n".join(parts).strip() + "\n"


def local_source(video: dict) -> Path:
    """The highest-quality local file behind this entry."""
    for src in video.get("sources", []):
        if src["src"].endswith(".mp4"):
            return HOME / "rappvision" / src["src"]
    return HOME / "rappvision" / video["sources"][0]["src"]


def reformat(video: dict, fmt: str, out: Path) -> Path:
    """Retarget a script-based video to another aspect ratio without losing pixels.

    The obvious approach — rewrite the composition's root data-width/data-height
    and re-render — produces a 1080x1080 file whose layout is still the 1920-wide
    one, simply CUT OFF at the right edge. The render succeeds, ffprobe reports
    the requested dimensions, and the output is quietly broken. That is the same
    green-while-frozen failure this whole channel is about, so it is not what
    this does.

    Unless a composition is authored responsively, the only lossless retarget is
    to fit the whole frame into the new canvas and pad the remainder. Nothing is
    cropped, all type stays legible, and the result is honest about being a 16:9
    piece presented in a square or vertical frame.
    """
    src = local_source(video)
    if not src.is_file():
        raise SystemExit(f"source media missing: {src}")
    w, h, _ = FORMATS[fmt]
    # scale to fit inside the target, keep aspect, pad the rest, keep even dims
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x0B0D0E,"
          f"setsar=1")
    print(f"  retargeting to {w}x{h} (fit and pad, nothing cropped)")
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vf", vf,
         "-c:v", "libx264", "-preset", "slow", "-crf", "19",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "aac", "-b:a", "160k", str(out), "-y"],
        capture_output=True, text=True)
    if r.returncode != 0 or not out.is_file():
        raise SystemExit(f"retarget failed for {video['id']} at {fmt}:\n{r.stderr[-600:]}")

    # Verify the output is actually the shape we asked for rather than trusting
    # the exit code — a zero exit with the wrong dimensions is exactly the class
    # of silent success this function exists to avoid.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(out)],
        capture_output=True, text=True)
    got = (probe.stdout or "").strip()
    if got != f"{w}x{h}":
        raise SystemExit(f"retarget produced {got}, expected {w}x{h}")
    print(f"  verified {got}")
    return out


def export(video: dict, fmt: str) -> Path:
    kind = (video.get("source") or {}).get("kind", "script")
    duration = float(video.get("duration", 0))

    if fmt != "landscape":
        if kind == "recording":
            raise SystemExit(
                f'"{video["id"]}" is a burned-in recording. Its framing is baked\n'
                "  into the pixels, so exporting it as "
                f"{fmt} could only crop — which would\n"
                "  cut off captions and lower thirds. Export it as landscape.")
        if fmt == "shorts" and duration > SHORTS_MAX_SECONDS:
            raise SystemExit(
                f'"{video["id"]}" is {duration:.0f}s. YouTube Shorts caps at '
                f"{SHORTS_MAX_SECONDS}s.\n  Export as square, or cut a shorter piece first.")

    dest = OUTDIR / f"{video['id']}--{fmt}"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{video['id']}-{fmt}.mp4"

    if fmt == "landscape":
        src = local_source(video)
        if not src.is_file():
            raise SystemExit(f"source media missing: {src}")
        shutil.copy2(src, target)
    else:
        reformat(video, fmt, target)

    thumb = HOME / "rappvision" / video["thumb"]
    if thumb.is_file():
        shutil.copy2(thumb, dest / f"thumbnail{thumb.suffix}")

    (dest / "title.txt").write_text(video["title"].strip() + "\n", encoding="utf-8")
    (dest / "description.txt").write_text(build_description(video), encoding="utf-8")
    (dest / "tags.txt").write_text(", ".join(video.get("tags", [])) + "\n", encoding="utf-8")

    problems = check_chapters(video.get("chapters") or [], duration)
    w, h, label = FORMATS[fmt]
    readme = [
        f"# {video['title']}", "",
        f"Format: {label}  ({w}x{h})",
        f"Duration: {duration:.1f}s",
        f"Source kind: {kind}", "",
        "## Upload", "",
        "1. Upload the .mp4",
        "2. Paste title.txt as the title",
        "3. Paste description.txt as the description — chapters are already in it",
        "4. Paste tags.txt into tags",
        "5. Upload thumbnail.jpg as the custom thumbnail", "",
    ]
    if fmt == "shorts":
        readme += ["Shorts is inferred from the 9:16 ratio and sub-3-minute length.",
                   "You do not need to add #shorts, but it does not hurt.", ""]
    if problems:
        readme += ["## Chapters will NOT render", "",
                   "YouTube silently shows no chapters at all when any rule is broken:", ""]
        readme += [f"- {p}" for p in problems]
        readme.append("")
    else:
        readme += [f"## Chapters OK ({len(video.get('chapters', []))} entries)", ""]
    (dest / "README.md").write_text("\n".join(readme), encoding="utf-8")

    print(f"  → {dest.relative_to(HOME)}")
    if problems:
        print("    ⚠ chapters will not render: " + "; ".join(problems))
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", nargs="?", help="video id from channel.json")
    ap.add_argument("--format", default="landscape", choices=sorted(FORMATS))
    ap.add_argument("--all", action="store_true", help="export every video")
    ap.add_argument("--list", action="store_true", help="list videos and exit")
    args = ap.parse_args()

    channel = load_channel()
    videos = channel["videos"]

    if args.list:
        for v in videos:
            kind = (v.get("source") or {}).get("kind", "script")
            can = "landscape, square, shorts" if kind == "script" else "landscape only"
            print(f"  {v['id']:26s} {v['duration']:>6.1f}s  {kind:9s}  → {can}")
        return 0

    if args.all:
        targets = videos
    elif args.id:
        targets = [v for v in videos if v["id"] == args.id]
        if not targets:
            print(f"no video with id {args.id!r}", file=sys.stderr)
            return 1
    else:
        ap.print_help()
        return 1

    print(f"exporting {len(targets)} video(s) as {args.format}")
    for v in targets:
        export(v, args.format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
