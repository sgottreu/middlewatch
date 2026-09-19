"""Stages as detached child processes, so a paid run outlives the page.

Why not threads. `mw-deploy` restarts the review server whenever Python changes,
and a thread dies with it — halfway through a narration that has already been
paid for, leaving the manifest claiming a run that no longer exists. A child in
its own process group outlives the request, the browser tab, and the server
itself; it keeps appending to its log and is still there to report on afterwards.
It is also the only version of this where Cancel means anything: killing a
process group stops the provider call and ffmpeg with it. A thread has no stop.

State is files, like the bundles themselves:

    runs/<slug>/<job_id>/meta.json     stage, pid, status, timings, flags
    runs/<slug>/<job_id>/output.log    stdout and stderr, append-only
    runs/<slug>/<job_id>/note.txt      redraft only — the note, as sent

Nothing is held in memory, so a restarted server reports on jobs it never
started and a finished job's log is still readable days later. What this module
deliberately does not do is interpret any of it: progress markers come back as
they were logged, and the caller decides what they mean.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# One line per event, emitted by the CLI under MW_PROGRESS. Progress has to
# cross a process boundary now, and parsing human output would make every
# print() in the pipeline load-bearing.
MARKER = "::progress "

STAGES = ("write", "redraft", "record", "design")

# How much log a page gets when it asks from the start. Enough to see what
# happened, not so much that a long write blocks first paint.
LOG_TAIL = 64_000


def runs_dir() -> Path:
    """Job state, at the repo root rather than inside a bundle.

    Not under `stories/`: `mw-commit-stories` commits that directory wholesale
    every fifteen minutes, and a job log is not approval history.
    """
    return Path(os.environ.get("MW_RUNS_DIR") or "runs")


# meta.json has more than one writer — the reaper thread recording an exit code,
# a cancel, and any read that settles a dead job — so read-modify-write is
# serialised. Re-entrant because settling happens inside other operations.
_STATE_LOCK = threading.RLock()


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path: Path, meta: dict) -> None:
    # Unique temp name per writer: a shared one means two concurrent writes race
    # to replace the same file, and the loser raises FileNotFoundError.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(meta, indent=2) + "\n")
    tmp.replace(path)


def alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process now wearing the same pid. Not ours, so not running.
        return False
    except (TypeError, ValueError):
        return False
    return True


def _settle(meta_path: Path, meta: dict) -> dict:
    """Reconcile a meta that claims to be running against the actual process.

    This is what makes a killed server harmless: the next read notices the pid
    is gone and records the job as failed, rather than leaving the UI polling a
    job that stopped hours ago.
    """
    with _STATE_LOCK:
        meta = _read(meta_path) or meta
        if meta.get("status") == "running" and not alive(meta.get("pid")):
            meta["status"] = "failed"
            meta["finished"] = meta.get("finished") or time.time()
            meta.setdefault("exit_code", None)
            meta["died"] = True
            _write(meta_path, meta)
        return meta


def _dirs(slug: str) -> list[Path]:
    d = runs_dir() / slug
    if not d.is_dir():
        return []
    return sorted(p.parent for p in d.glob("*/meta.json"))


def latest(slug: str) -> dict | None:
    dirs = _dirs(slug)
    if not dirs:
        return None
    d = dirs[-1]
    return _settle(d / "meta.json", _read(d / "meta.json")) or None


def job_dir(slug: str, job_id: str) -> Path:
    return runs_dir() / slug / job_id


def start(cfg: dict, slug: str, stage: str, *, force: bool = False,
          restart: bool = False, replan: bool = False, chapter=None,
          note: str = "") -> dict:
    """Launch one stage as a detached process and return its meta."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")

    current = latest(slug)
    if current and current.get("status") == "running":
        raise ValueError(
            f"{slug} already has a {current['stage']} running. Cancel it first "
            "if you want to start something else."
        )

    root = Path(cfg["stories_dir"]) / slug
    # Timestamp first so a plain sort is chronological — the stage name leading
    # would sort `design` before `write` regardless of when either ran.
    stamp = time.strftime("%Y%m%dT%H%M%S")
    job_id = f"{stamp}-{stage}"
    d = job_dir(slug, job_id)
    n = 2
    while d.exists():
        job_id = f"{stamp}-{stage}-{n}"
        d = job_dir(slug, job_id)
        n += 1
    d.mkdir(parents=True)

    argv = [sys.executable, "-m", "story_pipeline.cli", stage, str(root)]
    if stage == "write":
        if force:
            argv.append("--force")
        if restart:
            argv.append("--restart")
    elif stage == "record":
        if force:
            argv.append("--force")
    elif stage == "design":
        if replan:
            argv.append("--replan")
    elif stage == "redraft":
        # Through a file rather than argv: it is prose with newlines in it, and
        # keeping it beside the log makes the run self-describing afterwards.
        (d / "note.txt").write_text(note or "")
        argv += ["--chapter", str(int(chapter)),
                 "--note-file", str((d / "note.txt").resolve())]

    meta = {
        "job_id": job_id, "slug": slug, "stage": stage, "kind": stage,
        "argv": argv, "status": "running", "started": time.time(),
        "finished": None, "exit_code": None, "pid": None, "chapter": chapter,
        "force": force, "restart": restart, "replan": replan,
    }

    log = (d / "output.log").open("ab")
    try:
        proc = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, cwd=os.getcwd(),
            # Its own session and process group. The child is not taken down
            # with the server, and cancel can signal the whole group — the
            # provider call, ffmpeg, and anything else it started.
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "MW_PROGRESS": "1"},
        )
    finally:
        # The child holds its own descriptor; this one would otherwise keep the
        # file open for the life of the server.
        log.close()

    meta["pid"] = proc.pid
    _write(d / "meta.json", meta)
    threading.Thread(target=_reap, args=(proc, d / "meta.json"),
                     daemon=True).start()
    return meta


def _reap(proc: subprocess.Popen, meta_path: Path) -> None:
    """Record how the child ended, while this server is still here to see it.

    An optimisation, not the source of truth — it is the only way to learn the
    exit code, but if the server is restarted first, `_settle` reaches the same
    verdict from the pid alone.
    """
    rc = proc.wait()
    with _STATE_LOCK:
        meta = _read(meta_path)
        # Re-read under the lock: a cancel that landed while this was waiting
        # already wrote the verdict, and "cancelled" is the truer word for it
        # than the non-zero exit the signal produces.
        if meta.get("status") == "running":
            meta["status"] = "done" if rc == 0 else "failed"
            meta["exit_code"] = rc
            meta["finished"] = time.time()
            _write(meta_path, meta)


def cancel(slug: str) -> dict:
    with _STATE_LOCK:
        meta = latest(slug)
        if not meta or meta.get("status") != "running":
            raise ValueError(f"nothing is running for {slug}.")
        pid = meta.get("pid")
        try:
            # The group, not the pid: the stage's own children are what actually
            # hold the work — and the money — open.
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, TypeError, ValueError):
            # Already gone, or gone between the check and the signal. Recording
            # the intent still beats leaving it as a job that says it is running.
            pass
        meta["status"] = "cancelled"
        meta["finished"] = time.time()
        _write(job_dir(slug, meta["job_id"]) / "meta.json", meta)
        return meta


def reap_dead() -> list[dict]:
    """Settle every job left claiming to be running. Call this on server start.

    Without it, a crash or a deploy leaves jobs spinning in the UI forever.
    """
    settled = []
    root = runs_dir()
    if not root.is_dir():
        return settled
    for meta_path in root.glob("*/*/meta.json"):
        meta = _read(meta_path)
        if meta.get("status") != "running":
            continue
        before = dict(meta)
        meta = _settle(meta_path, meta)
        if meta.get("status") != before.get("status"):
            settled.append(meta)
    return settled


def markers(slug: str, job_id: str | None = None) -> list[dict]:
    """Every progress record the stage has emitted so far."""
    meta = latest(slug) if job_id is None else {"job_id": job_id}
    if not meta:
        return []
    log = job_dir(slug, meta["job_id"]) / "output.log"
    if not log.exists():
        return []
    out = []
    with log.open("r", errors="replace") as fh:
        for line in fh:
            if line.startswith(MARKER):
                try:
                    out.append(json.loads(line[len(MARKER):]))
                except json.JSONDecodeError:
                    pass
    return out


def tail_error(slug: str, lines: int = 6) -> str:
    """The last thing the log said, for a job that ended badly."""
    meta = latest(slug)
    if not meta:
        return ""
    log = job_dir(slug, meta["job_id"]) / "output.log"
    if not log.exists():
        return ""
    text = log.read_text(errors="replace")
    keep = [l for l in text.splitlines()
            if l.strip() and not l.startswith(MARKER)]
    return "\n".join(keep[-lines:])


def status(slug: str) -> dict:
    meta = latest(slug)
    if not meta:
        return {"state": "none"}
    out = {
        "state": meta.get("status", "none"),
        "kind": meta.get("stage"),
        "job_id": meta.get("job_id"),
        "started": meta.get("started"),
        "finished": meta.get("finished"),
        "exit_code": meta.get("exit_code"),
        "restart": meta.get("restart"),
        "progress": markers(slug),
        "can_cancel": meta.get("status") == "running",
    }
    if meta.get("chapter") is not None:
        out["n"] = meta["chapter"]
    if out["state"] in ("failed", "cancelled"):
        tail = tail_error(slug)
        if out["state"] == "cancelled":
            out["error"] = tail or "stopped"
        elif meta.get("died"):
            # No exit code to report: the process was already gone by the time
            # anything looked, which is what a kill -9 or a reboot looks like.
            out["error"] = tail or ("the stage stopped without reporting an "
                                    "error — the machine or the process was "
                                    "killed under it")
        else:
            out["error"] = tail or f"exited {meta.get('exit_code')}"
    return out


def log_chunk(slug: str, offset: int = 0) -> dict:
    """Log text from `offset`, for a page tailing a running stage.

    Returns whole lines only and reports where it stopped, so the next call
    resumes there rather than replaying the run.
    """
    meta = latest(slug)
    if not meta:
        return {"offset": 0, "text": "", "job_id": None, "state": "none"}
    log = job_dir(slug, meta["job_id"]) / "output.log"
    if not log.exists():
        return {"offset": 0, "text": "", "job_id": meta["job_id"],
                "state": meta.get("status")}

    size = log.stat().st_size
    start_at = int(offset or 0)
    if start_at <= 0 and size > LOG_TAIL:
        start_at = size - LOG_TAIL
    start_at = max(0, min(start_at, size))

    with log.open("rb") as fh:
        fh.seek(start_at)
        raw = fh.read()

    # Stop at the last newline: a half-written line now would be shown again,
    # differently, on the next poll.
    cut = raw.rfind(b"\n") + 1
    consumed, raw = cut, raw[:cut]
    text = raw.decode("utf-8", errors="replace")
    # Markers are for the progress bar, not for reading.
    shown = "\n".join(l for l in text.splitlines() if not l.startswith(MARKER))
    return {"offset": start_at + consumed, "text": shown,
            "job_id": meta["job_id"], "state": meta.get("status")}
