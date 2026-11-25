"""archive.py with the aws CLI faked."""
import json
import subprocess
from pathlib import Path

import pytest

from story_pipeline import archive
from story_pipeline.archive import (ArchiveError, archive_story, cmd_archive,
                                    find_story, local_assets, register, remote_sizes)
from tests.pipeline.conftest import make_mini, ns


class FakeAws:
    """Stands in for subprocess.run(['aws', ...]). `bucket` is what S3 holds."""

    def __init__(self, remote=None, rc=0, stderr=""):
        self.calls, self.remote, self.rc, self.stderr = [], remote, rc, stderr

    def __call__(self, cmd, capture_output=False, text=False):
        self.calls.append(cmd)
        out = ""
        if "list-objects-v2" in cmd:
            prefix = cmd[cmd.index("--prefix") + 1]
            out = json.dumps({"Contents": [{"Key": prefix + k, "Size": v}
                                           for k, v in (self.remote or {}).items()]})
        return subprocess.CompletedProcess(cmd, self.rc, stdout=out, stderr=self.stderr)


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setattr(archive.shutil, "which", lambda n: "/usr/bin/aws")
    fake = FakeAws()
    monkeypatch.setattr(archive.subprocess, "run", fake)
    return fake


@pytest.fixture
def assets_story(mini):
    (mini.root / "audio" / "01.mp3").write_bytes(b"a" * 10)
    (mini.root / "images" / "01-00.JPG").write_bytes(b"b" * 20)
    (mini.root / "images" / "cast" / "ann.png").write_bytes(b"c" * 5)
    (mini.root / "video" / "final.mp4").write_bytes(b"d" * 7)
    (mini.root / "video" / "subtitles.srt").write_text("not an asset")
    return mini


def test_aws_missing(monkeypatch):
    monkeypatch.setattr(archive.shutil, "which", lambda n: None)
    with pytest.raises(ArchiveError, match="not on PATH"):
        archive._aws(None, "s3", "ls")


def test_aws_profile_appended_and_failure(aws):
    archive._aws("prof", "s3", "ls")
    assert aws.calls[0] == ["aws", "s3", "ls", "--profile", "prof"]
    aws.rc, aws.stderr = 2, "boom"
    with pytest.raises(ArchiveError, match=r"exit 2\). boom"):
        archive._aws(None, "s3", "ls", capture=True)


def test_find_story_variants(repo_root, mini):
    stories = repo_root / "stories"
    assert find_story(stories, "mini") == stories / "standalone" / "mini"
    assert find_story(stories, "mini", arc="standalone") == stories / "standalone" / "mini"
    assert find_story(stories, "stories/standalone/mini") == Path("stories/standalone/mini")
    with pytest.raises(ArchiveError, match="No story bundle at"):
        find_story(stories, "stories/standalone/ghost")
    with pytest.raises(ArchiveError, match="No story folder"):
        find_story(stories, "mini", arc="other")
    with pytest.raises(ArchiveError, match="No stories/<arc>/ghost"):
        find_story(stories, "ghost")


def test_find_story_ambiguous(repo_root):
    for arc in ("a", "b"):
        (repo_root / "stories" / arc / "dup").mkdir(parents=True)
    with pytest.raises(ArchiveError, match="several arcs .*pass --arc"):
        find_story(repo_root / "stories", "dup")


def test_local_assets_only_asset_extensions(assets_story):
    got = local_assets(assets_story.root)
    assert got == {"audio/01.mp3": 10, "images/01-00.JPG": 20,
                   "images/cast/ann.png": 5, "video/final.mp4": 7}


def test_remote_sizes(aws):
    aws.remote = {"audio/01.mp3": 10}
    assert remote_sizes("bkt", "stories/a/b/", None) == {"audio/01.mp3": 10}
    aws.remote = {}
    assert remote_sizes("bkt", "p/", None) == {}


def test_archive_no_assets_raises(mini, aws):
    with pytest.raises(ArchiveError, match="No asset files"):
        archive_story("mini", bucket="b", profile=None, stories=Path("stories"))


def test_archive_no_assets_but_record_returns_record(mini, aws):
    rec = {"bucket": "b"}
    (mini.root / "archive.json").write_text(json.dumps(rec))
    logs = []
    assert archive_story("mini", bucket="b", profile=None, stories=Path("stories"),
                         log=logs.append) == rec
    assert "already in S3" in logs[0]


def test_dry_run_changes_nothing(assets_story, aws):
    logs = []
    r = archive_story("mini", bucket="bkt", profile=None, stories=Path("stories"),
                      dry_run=True, delete_local=True, log=logs.append)
    assert r is None
    assert "--dryrun" in aws.calls[0] and len(aws.calls) == 1
    assert "--delete-local ignored" in logs[-1]
    assert not (assets_story.root / "archive.json").exists()
    assert (assets_story.root / "audio" / "01.mp3").exists()
    # sync includes both cases of every extension
    sync = aws.calls[0]
    assert "s3://bkt/stories/standalone/mini/" in sync
    assert sync.count("--include") == 2 * len(archive.ASSET_EXTS)


def test_archive_verify_ok_writes_record(assets_story, aws):
    aws.remote = local_assets(assets_story.root)
    r = archive_story("mini", bucket="bkt", profile="p", stories=Path("stories"), log=lambda *_: None)
    assert r["files"] == 4 and r["bytes"] == 42 and r["bucket"] == "bkt"
    assert r["prefix"] == "stories/standalone/mini/" and r["local_copies_deleted"] is False
    on_disk = json.loads((assets_story.root / "archive.json").read_text())
    assert on_disk == r
    assert (assets_story.root / "audio" / "01.mp3").exists()
    assert all("--profile" in c for c in aws.calls)


def test_archive_verification_failure_deletes_nothing(assets_story, aws):
    remote = local_assets(assets_story.root)
    remote["audio/01.mp3"] = 999          # wrong size
    del remote["video/final.mp4"]         # missing
    aws.remote = remote
    logs = []
    with pytest.raises(ArchiveError, match="2 of 4 files failed verification"):
        archive_story("mini", bucket="b", profile=None, stories=Path("stories"),
                      delete_local=True, log=logs.append)
    assert any("s3 missing" in l for l in logs)
    assert (assets_story.root / "audio" / "01.mp3").exists()
    assert not (assets_story.root / "archive.json").exists()


def test_archive_delete_local_removes_assets_and_empty_dirs(assets_story, aws):
    aws.remote = local_assets(assets_story.root)
    r = archive_story("mini", bucket="b", profile=None, stories=Path("stories"),
                      delete_local=True, log=lambda *_: None)
    assert r["local_copies_deleted"] is True
    root = assets_story.root
    assert not (root / "audio").exists() and not (root / "images").exists()
    assert (root / "video" / "subtitles.srt").exists()      # non-asset survives, so does its dir
    assert (root / "chapters" / "01.json").exists()
    assert (root / "archive.json").exists()


def test_cmd_archive_resolution_order(assets_story, aws, monkeypatch):
    seen = {}
    monkeypatch.setattr(archive, "archive_story", lambda slug, **kw: seen.update(kw, slug=slug))
    monkeypatch.delenv("MIDDLEWATCH_S3_BUCKET", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    args = dict(slug="mini", arc=None, dry_run=True, delete_local=False, bucket=None, profile=None)
    cmd_archive(ns(**args), {"stories_dir": "stories"})
    assert seen["bucket"] == archive.DEFAULT_BUCKET and seen["profile"] is None
    assert seen["stories"] == Path("stories") and seen["dry_run"] is True
    monkeypatch.setenv("MIDDLEWATCH_S3_BUCKET", "envb")
    monkeypatch.setenv("AWS_PROFILE", "envp")
    cmd_archive(ns(**args), {"stories_dir": "stories"})
    assert (seen["bucket"], seen["profile"]) == ("envb", "envp")
    cmd_archive(ns(**args), {"stories_dir": "stories", "s3": {"bucket": "cfgb", "profile": "cfgp"}})
    assert (seen["bucket"], seen["profile"]) == ("cfgb", "cfgp")
    cmd_archive(ns(**{**args, "bucket": "flag", "profile": "fp"}),
                {"stories_dir": "stories", "s3": {"bucket": "cfgb"}})
    assert (seen["bucket"], seen["profile"]) == ("flag", "fp")


def test_cmd_archive_error_exits_1(repo_root, aws, capsys):
    with pytest.raises(SystemExit) as e:
        cmd_archive(ns(slug="ghost", arc=None, dry_run=False, delete_local=False,
                       bucket="b", profile=None), {"stories_dir": "stories"})
    assert e.value.code == 1
    assert "archive: " in capsys.readouterr().err


def test_register_adds_subcommand():
    import argparse
    p = argparse.ArgumentParser()
    register(p.add_subparsers(dest="cmd"))
    a = p.parse_args(["archive", "x", "--arc", "a", "--bucket", "b", "--dry-run", "--delete-local"])
    assert a.fn is cmd_archive and a.arc == "a" and a.dry_run and a.delete_local
