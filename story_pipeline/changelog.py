"""Per-bible change history.

A bible is the one file in this pipeline that is edited by hand over years and
also written to by the machine — the designer promotes a generated portrait into
the series the first time a character is drawn, and nothing else records that it
happened. Six months later, "why does Mrs Pike look like that" has no answer.

The log lives beside the bible as `bibles/<name>.changelog.md`, matching the
`<name>.cast/` convention. It is deliberately *not* frontmatter: the bible's YAML
is hand-edited constantly and an unbounded list at the top of it would be in the
way. Nothing in the pipeline reads the changelog — it is for you.

Each entry carries a short hash of the bible's content at the time it was
written, which is what lets `bible check` notice that the file has been edited
since the last entry. Without that the log rots quietly, which is the usual fate
of a changelog nobody is reminded to update.
"""

from __future__ import annotations

import getpass
import hashlib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HEADER = """# Changelog — {series}

Newest first. Every entry records who made the change and a short hash of the
bible as it stood afterwards.

Append with:

    python3 -m story_pipeline.cli bible log {name} -m "what changed"

Entries written by the pipeline itself are attributed to `pipeline` and name the
story that caused them.
"""

# "## 2026-08-29 14:03 · Scott · edit · sha=4f2a9c1e"
ENTRY_RE = re.compile(
    r"^## (?P<when>[\d\-]+ [\d:]+) · (?P<author>[^·]+?) · (?P<kind>[^·]+?) · sha=(?P<sha>\w+)\s*$"
)


@dataclass
class Entry:
    when: str
    author: str
    kind: str
    sha: str
    summary: str


def path_for(bible_path: Path) -> Path:
    return bible_path.with_suffix("").with_name(bible_path.stem + ".changelog.md")


def content_hash(bible_path: Path) -> str:
    """Short hash of the bible's bytes. Only ever compared to itself, so a short
    digest is plenty and stays readable in the file."""
    return hashlib.sha256(bible_path.read_bytes()).hexdigest()[:8]


def resolve_author(explicit: str | None = None, cfg: dict | None = None) -> str:
    """Who to credit, most deliberate source first.

    `--author` beats config, which beats git, which beats the login name. Git is
    consulted because it is usually already configured with a real name, and a
    changelog full of `scottgottreu` is less useful than one full of `Scott
    Gottreu`.
    """
    if explicit:
        return explicit.strip()
    if cfg and cfg.get("author"):
        return str(cfg["author"]).strip()
    try:
        out = subprocess.run(["git", "config", "user.name"], capture_output=True,
                             text=True, timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def entries(bible_path: Path) -> list[Entry]:
    """Parse the log, newest first. A malformed file yields nothing rather than
    raising — a changelog is never worth failing a run over."""
    path = path_for(bible_path)
    if not path.exists():
        return []
    found: list[Entry] = []
    current: Entry | None = None
    body: list[str] = []
    for line in path.read_text().splitlines():
        m = ENTRY_RE.match(line)
        if m:
            if current:
                current.summary = "\n".join(body).strip()
                found.append(current)
            current = Entry(m["when"], m["author"].strip(), m["kind"].strip(),
                            m["sha"], "")
            body = []
        elif current is not None:
            body.append(line)
    if current:
        current.summary = "\n".join(body).strip()
        found.append(current)
    return found


def append(bible_path: Path, summary: str, author: str, kind: str = "edit",
           series: str | None = None) -> Path:
    """Add an entry at the top, creating the file if it does not exist."""
    path = path_for(bible_path)
    sha = content_hash(bible_path)
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"## {when} · {author} · {kind} · sha={sha}\n\n{summary.strip()}\n"

    if path.exists():
        text = path.read_text()
        split = text.index("\n## ") + 1 if "\n## " in text else len(text)
        head, rest = text[:split].rstrip("\n"), text[split:]
        path.write_text(f"{head}\n\n{entry}\n{rest}".rstrip("\n") + "\n")
    else:
        head = HEADER.format(series=series or bible_path.stem, name=bible_path.stem)
        path.write_text(f"{head}\n---\n\n{entry}")
    return path


def last_sha(bible_path: Path) -> str | None:
    found = entries(bible_path)
    return found[0].sha if found else None


def is_stale(bible_path: Path) -> bool:
    """True when the bible has been edited since the newest entry was written.

    A bible with no log at all is not stale — it has simply never been logged,
    which is reported separately so a first run doesn't nag about history that
    was never kept.
    """
    recorded = last_sha(bible_path)
    return bool(recorded) and recorded != content_hash(bible_path)
