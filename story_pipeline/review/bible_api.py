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
