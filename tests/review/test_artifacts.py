"""Artifact markers: what a stage announces when it writes a file, the URL the
review server serves it at, and how common.js renders it in a job panel."""
from __future__ import annotations

import json
import shutil

import pytest

from ambience_pipeline import cli as ambience_cli
from story_pipeline import cli as story_cli
from story_pipeline.review import ambience_api, runner

from .test_common_assets import _node

has_node = shutil.which("node") is not None


def _markers(text: str) -> list[dict]:
    return [json.loads(l[len(runner.MARKER):]) for l in text.splitlines()
            if l.startswith(runner.MARKER)]


# --------------------------------------------------------------- emitting --

def test_emit_artifact_is_silent_outside_a_job(monkeypatch, capsys):
    monkeypatch.delenv("MW_PROGRESS", raising=False)
    story_cli.emit_artifact("stories/x/audio/ch01.mp3")
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("path,kind", [("stories/x/audio/ch01.mp3", "audio"),
                                       ("stories/x/audio/ch01.WAV", "audio"),
                                       ("stories/x/images/shot-003.png", "image"),
                                       ("stories/x/video/final.mp4", "file"),
                                       ("stories/x/timeline.json", "file")])
def test_emit_artifact_guesses_kind_from_suffix(monkeypatch, capsys, path, kind):
    monkeypatch.setenv("MW_PROGRESS", "1")
    story_cli.emit_artifact(path)
    (m,) = _markers(capsys.readouterr().out)
    assert m == {"event": "artifact", "kind": kind, "path": path}


def test_emit_artifact_kind_and_label_override(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MW_PROGRESS", "1")
    story_cli.emit_artifact(tmp_path / "cast.png", kind="image", label="Cast sheet")
    (m,) = _markers(capsys.readouterr().out)
    assert m == {"event": "artifact", "kind": "image", "path": str(tmp_path / "cast.png"),
                 "label": "Cast sheet"}


def test_story_and_ambience_emit_the_same_marker_line(monkeypatch, capsys):
    monkeypatch.setenv("MW_PROGRESS", "1")
    ambience_cli._progress(event="artifact", kind="audio", path="ambience/a/audio/mix.wav")
    amb = capsys.readouterr().out
    story_cli.emit_artifact("ambience/a/audio/mix.wav")
    assert capsys.readouterr().out == amb
    assert amb.startswith(runner.MARKER) and amb.endswith("\n")


# ---------------------------------------------------------------- serving --

@pytest.fixture
def wav(repo_root):
    p = repo_root / "ambience" / "the-rain" / "audio" / "preview.wav"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"RIFF" + b"\0" * 96)
    (repo_root / "secret.wav").write_bytes(b"RIFFsecret")
    (p.parent / "mix.partial.wav").write_bytes(b"RIFF")
    (p.parent / "notes.txt").write_text("x")
    return p


def test_artifact_path_maps_to_the_route_that_serves_it(client, cfg, wav):
    url = ambience_api.artifact_url(cfg, "ambience/the-rain/audio/preview.wav")
    assert url == "/ambience-audio/the-rain/audio/preview.wav"
    assert ambience_api.artifact_url(cfg, wav) == url          # absolute path too
    r = client.get(url)
    assert r.status == 200 and r.body[:4] == b"RIFF"


@pytest.mark.parametrize("path", [
    "secret.wav",
    "ambience/../secret.wav",
    "ambience/the-rain/audio/../../../secret.wav",
    "/etc/passwd",
    "ambience/the-rain/audio/mix.partial.wav",
    "ambience/the-rain/audio/notes.txt",
    "ambience/the-rain/audio/missing.wav",
    "ambience/the-rain/manifest.json",
])
def test_artifact_paths_outside_what_the_route_serves_get_no_url(cfg, wav, path):
    assert ambience_api.artifact_url(cfg, path) is None


def test_job_view_lists_artifacts_newest_first_once_each(cfg, wav, monkeypatch):
    lines = "".join(runner.MARKER + json.dumps(m) + "\n" for m in [
        {"event": "plan", "total": 2},
        {"event": "artifact", "kind": "audio", "path": "ambience/the-rain/audio/preview.wav"},
        {"event": "artifact", "kind": "audio", "path": "secret.wav"},
        {"event": "artifact", "kind": "audio", "path": "ambience/the-rain/audio/preview.wav",
         "label": "Preview"},
    ])
    marks = _markers(lines)
    monkeypatch.setattr(runner, "status", lambda key: {"state": "done", "kind": "sound",
                                                       "progress": list(marks)})
    j = ambience_api.job(cfg, "ambience-the-rain")
    assert "progress" not in j
    assert j["artifacts"] == [
        {"kind": "audio", "path": "ambience/the-rain/audio/preview.wav", "label": "Preview",
         "url": "/ambience-audio/the-rain/audio/preview.wav"},
        {"kind": "audio", "path": "secret.wav", "label": "secret.wav", "url": None},
    ]
    assert j["artifact"] == "/ambience-audio/the-rain/audio/preview.wav"


def test_job_view_with_no_artifacts_has_an_empty_list(client):
    assert client.get("/api/ambience/audition/job").json["artifacts"] == []


# -------------------------------------------------------------- rendering --

@pytest.mark.skipif(not has_node, reason="node not installed")
def test_common_js_parses_and_renders_artifacts():
    log = "\n".join([
        "rendering…",
        runner.MARKER + json.dumps({"event": "plan", "total": 3}),
        runner.MARKER + json.dumps({"event": "artifact", "kind": "audio",
                                    "path": "a/mix.wav", "url": "/ambience-audio/a/audio/mix.wav"}),
        runner.MARKER + '{"event": "artifact", "path": "x.png", "url": "/img/x.png"}',
        runner.MARKER + '{"event": "artifact", "path": "a/mix.wav", "url": "/ambience-audio/a/audio/mix.wav"}',
        runner.MARKER + '{"event": "artifact", "path": "t.json", "url": "javascript:alert(1)"}',
        runner.MARKER + '{"event": "artifact", "path": "n.txt", "url": "//evil.example/n.txt"}',
        runner.MARKER + '{"event": "artifact", "path": "<b>.mp3"',      # half a line
        "wrote a/mix.wav",
    ])
    got = json.loads(_node(f"""
      const fromText = MW.artifacts({json.dumps(log)});
      const fromJob = MW.artifacts([{{kind: "image", path: "s/c.png", url: "/c.png", label: "Cast <sheet>"}},
                                    {{kind: "file", path: "s/v.mp4", url: "/v.mp4"}},
                                    {{kind: "audio", path: "s/a.wav", url: null}}]);
      console.log(JSON.stringify({{
        fromText, fromJob,
        html: MW.renderArtifacts(fromText.concat(fromJob)),
        empty: [MW.artifacts(null), MW.artifacts(""), MW.renderArtifacts([])],
      }}));
    """))
    t = got["fromText"]
    # newest first, the re-announced mix.wav once at its newest place,
    # kinds guessed from the name when the marker has none
    assert [a["path"] for a in t] == ["n.txt", "t.json", "a/mix.wav", "x.png"]
    assert [a["kind"] for a in t] == ["file", "file", "audio", "image"]
    assert t[0]["url"] is None and t[1]["url"] is None        # unsafe URLs dropped
    assert t[2] == {"kind": "audio", "url": "/ambience-audio/a/audio/mix.wav",
                    "path": "a/mix.wav", "label": "mix.wav"}
    h = got["html"]
    assert '<audio controls preload="none" src="/ambience-audio/a/audio/mix.wav">' in h
    assert '<img src="/img/x.png"' in h and 'href="/img/x.png"' in h
    assert '<a href="/v.mp4" target="_blank" rel="noopener">v.mp4</a>' in h
    assert "Cast &lt;sheet&gt;" in h and "<sheet>" not in h
    assert "a.wav · not served" in h
    assert 'href="javascript' not in h and 'src="javascript' not in h
    assert 'href="//evil' not in h
    assert got["empty"] == [[], [], ""]


@pytest.mark.skipif(not has_node, reason="node not installed")
def test_job_panel_shows_artifacts_with_the_log_and_leaves_players_alone():
    got = json.loads(_node("""
      const node = () => { let html = "", sets = 0;
        return { hidden: false, textContent: "", style: {}, scrollTop: 0, scrollHeight: 0,
                 classList: { add() {}, remove() {}, toggle() {} },
                 get innerHTML() { return html; }, set innerHTML(v) { html = v; sets++; },
                 get sets() { return sets; } }; };
      const parts = {};
      const el = Object.assign(node(), { querySelector: s => (parts[s] ||= node()) });
      globalThis.fetch = async () => ({ ok: true, json: async () => ({ text: "", offset: 0 }) });
      const p = MW.jobPanel(el, { urls: () => ({ job: "/j", log: "/l", cancel: "/c" }) });
      parts[".log"].hidden = true;
      const job = { state: "done", artifacts: [
        { kind: "audio", path: "a/mix.wav", url: "/ambience-audio/a/audio/mix.wav" }] };
      p.show(job);
      const arts = parts[".artifacts"], before = { hidden: arts.hidden, sets: arts.sets };
      parts["[data-log]"].onclick();            // open the log
      const opened = arts.hidden;
      p.show(job); p.show(job);                  // polls with nothing new
      console.log(JSON.stringify({ before, opened, sets: arts.sets, html: arts.innerHTML }));
    """))
    assert got["before"] == {"hidden": True, "sets": 1}   # rendered, hidden with the log
    assert got["opened"] is False                          # shown when the log opens
    assert got["sets"] == 1                                # not rebuilt while unchanged
    assert '<audio controls preload="none" src="/ambience-audio/a/audio/mix.wav">' in got["html"]
