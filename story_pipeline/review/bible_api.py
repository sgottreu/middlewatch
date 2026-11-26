"""The review server's half of `bible promote`: preview, fill and apply.

Same logic as the CLI (story_pipeline/bible_promote.py), so the page and the
command line cannot disagree about what a promote does. Preview runs as a runner
job — `cli bible promote <name> --dry-run --json` in a child process — so it is
the same command you would type, and its result comes back as the one progress
marker that command prints. Fill and apply are quick file writes and run inline.

Jobs are keyed `_bible-<name>` under runs/, beside the stories' and casting's.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import runner
from .. import bible_promote as bp


def _root(cfg: dict) -> Path:
    return Path(cfg.get("bibles_dir") or "bibles")


def _key(name: str) -> str:
    return f"_bible-{name}"


def _draft(cfg: dict, name: str) -> str:
    bp.check_name(name)
    if not bp.draft_path(name, _root(cfg)).exists():
        raise ValueError(f"no draft {name}.draft.md in {_root(cfg)}/{name}/")
    return name


def job(cfg: dict, name: str) -> dict:
    """The preview job's state, with its result once it has one."""
    bp.check_name(name)
    st = runner.status(_key(name))
    found = [m for m in st.get("progress") or [] if m.get("event") == "preview"]
    st.pop("progress", None)
    st["name"] = name
    if found:
        st["preview"] = found[-1]["preview"]
        # A dry run exits 1 when bible check finds errors, which the runner
        # records as a failure. The preview itself worked; the errors are in it.
        if st.get("state") == "failed" and st.get("exit_code") == 1:
            st["state"] = "done"
            st.pop("error", None)
    return st


def log(cfg: dict, name: str, offset: int = 0) -> dict:
    return runner.log_chunk(_key(bp.check_name(name)), offset)


def preview_start(cfg: dict, name: str) -> dict:
    """Start a dry-run promote. Free: no model is called and nothing is written."""
    _draft(cfg, name)
    argv = [sys.executable, "-m", "story_pipeline.cli",
            "--config", cfg.get("_config_path", "config.yaml"),
            "bible", "promote", name, "--dry-run", "--json",
            "--bibles-dir", str(_root(cfg))]
    runner.start_command(_key(name), "promote-preview", argv,
                         busy=f"A preview of {name} is already running.",
                         bible=name)
    return {"ok": True, "job": job(cfg, name)}


def fill(cfg: dict, name: str, field: str, value) -> dict:
    """Write one missing detail into the draft, then preview again."""
    _draft(cfg, name)
    try:
        bp.fill(name, field, value, _root(cfg))
    except bp.PromoteError as e:
        raise ValueError(str(e))
    return preview_start(cfg, name)


def apply_plan(cfg: dict, name: str) -> dict:
    """What Apply would write and commit, for the confirm step. Free."""
    _draft(cfg, name)
    return bp.apply_plan(name, cfg, _root(cfg))


def push_waiting(cfg: dict, name: str) -> dict:
    """Push the commits already waiting on this branch — no new commit, never
    forced — then return the refreshed Apply plan."""
    _draft(cfg, name)
    root = _root(cfg)
    bible = bp.bible_path(name, root)
    try:
        out = bp.push_waiting([bp.draft_path(name, root), bible,
                               bp.changelog.path_for(bible)])
    except bp.PromoteError as e:
        raise ValueError(str(e))
    out["plan"] = bp.apply_plan(name, cfg, root)
    return out


def apply(cfg: dict, name: str, commit: bool = True, push: bool = True,
          author: str | None = None) -> dict:
    """Write `<name>.md`, log it, and commit the draft, bible and changelog —
    and push, never forced — unless told not to. Refuses on any fatal problem."""
    _draft(cfg, name)
    try:
        return bp.apply(name, cfg, author=author, do_commit=commit, push=push,
                        bibles_dir=_root(cfg))
    except bp.PromoteError as e:
        raise ValueError(str(e))


def view(cfg: dict, name: str) -> dict:
    """Everything the Bible view shows, for a series or a draft. Free, writes
    nothing.

    `draft` is the working draft as written. `agents` is the promoted bible as
    the agents receive it — the ideator's view, Story Ideas included — headed by
    the series' genre settings, which reach the ideator as its subgenre and
    trope menu rather than as prose. `stale` says whether promoting now would
    change the bible, so a draft edited since its last promote is visible.
    """
    from .. import bibles, genres  # local: keep this module's imports as they were

    bp.check_name(name)
    root = _root(cfg)
    dpath, bpath = bp.draft_path(name, root), bp.bible_path(name, root)
    if not dpath.exists() and not bpath.exists():
        raise ValueError(f"no bible or draft named {name!r} in {root}/")
    out: dict = {"name": name, "series": name,
                 "draft": dpath.read_text() if dpath.exists() else None,
                 "agents": None, "stale": None, "problems": []}
    if bpath.exists():
        try:
            b = bibles.load(name, root)
            out["series"] = b.series
            block = b.prompt_block(ideator=True)
            # "# Series bible: X" then the guide's own "# X": one title is enough here.
            head, _, rest = block.partition("\n")
            if rest.lstrip().startswith("# "):
                block = rest.lstrip()
            out["agents"] = _settings_block(b, genres) + block
            out["problems"] = [str(p).strip() for p in bibles.validate(b.path, cfg)
                               if p.fatal]
        except Exception as e:  # a bible that will not parse still gets its draft shown
            out["problems"] = [str(e)]
    if dpath.exists():
        try:
            out["stale"] = bp.build(name, root).changed
        except Exception:
            out["stale"] = None
    return out


def _settings_block(b, genres) -> str:
    genre_name = b.meta.get("genre", "")
    lines = ["# Genre settings", ""]
    if genre_name not in genres.available():
        return "\n".join(lines + [f"- Genre: {genre_name or 'not declared'}", "", ""])
    g = genres.load(genre_name)
    view = g.for_series(b.meta)
    lines.append(f"- **Genre:** {genre_name}")
    note = "" if b.meta.get("subgenres") else " (the whole genre; New outline uses the first)"
    lines.append(f"- **Subgenres:** {', '.join(view.subgenres)}{note}")
    if b.meta.get("exclude_tropes"):
        lines.append("- **Excluded tropes:** " + ", ".join(map(str, b.meta["exclude_tropes"])))
    if b.meta.get("series_tropes"):
        lines.append("- **Series tropes:**")
        lines += [f"  - **{t['name']}:** {t.get('note', '')}"
                  for t in b.meta["series_tropes"] if isinstance(t, dict) and t.get("name")]
    lines.append(f"- **Trope menu:** {len(view.tropes)} tropes, a sample of which "
                 "is offered each outline")
    return "\n".join(lines) + "\n\n"
