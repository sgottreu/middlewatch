"""Archive a story's assets (audio, graphics, video) to S3.

    python3 -m story_pipeline.cli archive the-cracked-beam --dry-run
    python3 -m story_pipeline.cli archive the-cracked-beam
    python3 -m story_pipeline.cli archive the-cracked-beam --delete-local
    python3 -m story_pipeline.cli archive stories/amberlight/the-cracked-beam

Uploads with `aws s3 sync`, then checks that every local asset is in S3 at the
same size. --delete-local removes only files that passed that check, and only
after all of them did. Writes archive.json into the story folder — that file is
text and belongs in git; it records where the assets went.

Bucket and profile come from, in order: the flags, cfg["s3"], the
MIDDLEWATCH_S3_BUCKET / AWS_PROFILE env vars, then the defaults below. No
profile means the CLI's own default chain — the instance role on the server.

Stories are found under cfg["stories_dir"], which resolves against the working
directory like every other command. Run from the repo root.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ASSET_EXTS = (".mp3", ".wav", ".flac", ".m4a",
              ".png", ".jpg", ".jpeg", ".webp",
              ".mp4", ".mov")
DEFAULT_BUCKET = "middlewatch-assets"
DEFAULT_PROFILE = None


class ArchiveError(RuntimeError):
    pass


# --- helpers ---------------------------------------------------------------

def _aws(profile: str | None, *args: str, capture: bool = False) -> str:
    if not shutil.which("aws"):
        raise ArchiveError("aws CLI is not on PATH. Install AWS CLI v2 first.")
    cmd = ["aws", *args] + (["--profile", profile] if profile else [])
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode:
        detail = (result.stderr or "").strip() if capture else ""
        raise ArchiveError(f"`{' '.join(cmd)}` failed (exit {result.returncode}). {detail}")
    return result.stdout if capture else ""


def find_story(stories: Path, slug: str, arc: str | None = None) -> Path:
    # The path form the other commands take, e.g. stories/amberlight/<slug>.
    given = Path(slug)
    if len(given.parts) > 1:
        if (given / "manifest.json").is_file():
            return given
        raise ArchiveError(f"No story bundle at {given}")
    if arc:
        path = stories / arc / slug
        if path.is_dir():
            return path
        raise ArchiveError(f"No story folder at {path}")
    hits = [p for p in stories.glob(f"*/{slug}") if p.is_dir()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ArchiveError(f"No stories/<arc>/{slug} under {stories}")
    raise ArchiveError(f"{slug} exists in several arcs ({', '.join(p.parent.name for p in hits)}); pass --arc")


def local_assets(story_dir: Path) -> dict[str, int]:
    """Relative path -> size in bytes, for asset files only."""
    return {p.relative_to(story_dir).as_posix(): p.stat().st_size
            for p in sorted(story_dir.rglob("*"))
            if p.is_file() and p.suffix.lower() in ASSET_EXTS}


def remote_sizes(bucket: str, prefix: str, profile: str | None) -> dict[str, int]:
    out = _aws(profile, "s3api", "list-objects-v2", "--bucket", bucket,
               "--prefix", prefix, "--output", "json", capture=True)
    data = json.loads(out) if out.strip() else {}
    return {o["Key"][len(prefix):]: o["Size"] for o in data.get("Contents") or []}


def _remove_empty_dirs(story_dir: Path) -> None:
    dirs = sorted((p for p in story_dir.rglob("*") if p.is_dir()),
                  key=lambda p: len(p.parts), reverse=True)
    for d in dirs:
        if not any(d.iterdir()):
            d.rmdir()


# --- the command -------------------------------------------------------------

def archive_story(slug: str, *, bucket: str, profile: str | None,
                  arc: str | None = None, stories: Path = Path("stories"),
                  dry_run: bool = False, delete_local: bool = False,
                  log=print) -> dict | None:
    story_dir = find_story(stories, slug, arc)
    slug, arc = story_dir.name, story_dir.parent.name
    prefix = f"stories/{arc}/{slug}/"
    record_path = story_dir / "archive.json"

    assets = local_assets(story_dir)
    if not assets:
        if record_path.exists():
            log(f"Nothing local to archive — {record_path} says it's already in S3.")
            return json.loads(record_path.read_text())
        raise ArchiveError(f"No asset files in {story_dir}")

    total = sum(assets.values())
    log(f"{len(assets)} asset files, {total / 1e6:.1f} MB -> s3://{bucket}/{prefix}")

    sync = ["s3", "sync", f"{story_dir}/", f"s3://{bucket}/{prefix}", "--exclude", "*"]
    for ext in ASSET_EXTS:
        sync += ["--include", f"*{ext}", "--include", f"*{ext.upper()}"]

    if dry_run:
        _aws(profile, *sync, "--dryrun")
        note = " (--delete-local ignored on a dry run)" if delete_local else ""
        log(f"Dry run: nothing uploaded, nothing deleted{note}.")
        return None

    _aws(profile, *sync)

    remote = remote_sizes(bucket, prefix, profile)
    bad = [rel for rel, size in assets.items() if remote.get(rel) != size]
    if bad:
        for rel in bad:
            log(f"  not verified: {rel} (local {assets[rel]} B, s3 {remote.get(rel, 'missing')})")
        raise ArchiveError(f"{len(bad)} of {len(assets)} files failed verification. Nothing deleted.")
    log(f"Verified {len(assets)} files in S3.")

    record = {
        "bucket": bucket,
        "prefix": prefix,
        "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": len(assets),
        "bytes": total,
        "local_copies_deleted": False,
    }

    if delete_local:
        for rel in assets:
            (story_dir / rel).unlink()
        _remove_empty_dirs(story_dir)
        record["local_copies_deleted"] = True
        log(f"Deleted {len(assets)} local copies ({total / 1e6:.1f} MB freed).")

    record_path.write_text(json.dumps(record, indent=2) + "\n")
    log(f"Wrote {record_path} — commit it.")
    return record


def _setting(cfg, key: str, env: str, default: str | None) -> str | None:
    try:
        from_cfg = (cfg.get("s3") or {}).get(key)
    except AttributeError:
        from_cfg = None
    return from_cfg or os.environ.get(env) or default


def cmd_archive(args, cfg) -> None:
    bucket = args.bucket or _setting(cfg, "bucket", "MIDDLEWATCH_S3_BUCKET", DEFAULT_BUCKET)
    profile = args.profile or _setting(cfg, "profile", "AWS_PROFILE", DEFAULT_PROFILE)
    try:
        archive_story(args.slug, bucket=bucket, profile=profile, arc=args.arc,
                      stories=Path(cfg["stories_dir"]), dry_run=args.dry_run, delete_local=args.delete_local)
    except ArchiveError as e:
        print(f"archive: {e}", file=sys.stderr)
        sys.exit(1)


def register(subparsers) -> None:
    p = subparsers.add_parser("archive", help="copy a story's audio/graphics/video to S3")
    p.add_argument("slug", help="story slug or bundle path, e.g. the-cracked-beam")
    p.add_argument("--arc", help="arc slug; only needed if the slug exists in more than one arc")
    p.add_argument("--bucket", help=f"S3 bucket (default {DEFAULT_BUCKET})")
    p.add_argument("--profile", help="AWS CLI profile (default: the CLI's own credential chain)")
    p.add_argument("--dry-run", action="store_true", help="show what would upload; change nothing")
    p.add_argument("--delete-local", action="store_true",
                   help="after every file is verified in S3, delete the local copies")
    p.set_defaults(fn=cmd_archive)
