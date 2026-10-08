"""The two steps after the video exists: approve it, then publish it.

Both are instant manifest writes, shared by `cli approve --video`, `cli publish`
and the review page, so the gates are enforced in one place:

    approve  needs `direct` done. Undo puts it back to awaiting_review.
    publish  needs `approve` approved (CLI --force skips that). Records the
             YouTube URL, the video id parsed from it, and the date.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import parse_qs, urlparse

from .bundle import Bundle

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id(url: str) -> str | None:
    """The 11-character id from any common YouTube URL form, or a bare id."""
    url = (url or "").strip()
    if _ID.match(url):
        return url
    u = urlparse(url if "//" in url else "https://" + url)
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        cand = u.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        if u.path == "/watch":
            cand = (parse_qs(u.query).get("v") or [""])[0]
        else:
            parts = u.path.strip("/").split("/")
            cand = parts[1] if len(parts) > 1 and parts[0] in (
                "shorts", "live", "embed", "v") else ""
    else:
        return None
    return cand if _ID.match(cand) else None


def approve_video(b: Bundle, undo: bool = False) -> str:
    if b.stage_status("direct") != "done":
        raise ValueError("there is no rendered video to approve yet — run `direct` first.")
    if undo:
        if b.stage_status("publish") == "done":
            raise ValueError("this story is already published; unpublish it first.")
        b.set_stage("approve", "awaiting_review")
    else:
        b.set_stage("approve", "approved")
    return b.stage_status("approve")


def publish(b: Bundle, url: str, when: str | None = None,
            force: bool = False, undo: bool = False) -> dict:
    if undo:
        b.set_stage("publish", "pending")
        m = b.manifest()
        m.get("stage_info", {}).pop("publish", None)
        b.write_manifest(m)
        return {}
    if b.stage_status("approve") != "approved" and not force:
        raise ValueError("approve the video before publishing it.")
    vid = youtube_id(url)
    if not vid:
        raise ValueError(f"not a YouTube video URL: {url!r}")
    when = when or date.today().isoformat()
    date.fromisoformat(when)  # raises on a malformed date
    info = {"url": f"https://youtu.be/{vid}", "video_id": vid, "published": when}
    b.set_stage("publish", "done", **info)
    return info
