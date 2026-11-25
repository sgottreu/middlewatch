"""tts.py: alignment -> sentences, ElevenLabs request building, Polly, build()."""
import base64
import copy
import json
import sys
import types

import pytest

from story_pipeline import config as cfgmod
from story_pipeline import tts
from story_pipeline.tts import (Clip, ElevenLabs, Polly, sentences_from_alignment,
                                strip_tags, tags_enabled)


def align(text, step=0.1):
    n = len(text)
    return {"characters": list(text),
            "character_start_times_seconds": [round(i * step, 3) for i in range(n)],
            "character_end_times_seconds": [round((i + 1) * step, 3) for i in range(n)]}


def texts(sents):
    return [s["text"] for s in sents]


# --- strip_tags / sentences_from_alignment ----------------------------------

def test_strip_tags():
    assert strip_tags("[quietly] Hello  there") == "Hello there"
    assert strip_tags("no tags") == "no tags"
    assert strip_tags("a [b] c") == "a c"
    assert strip_tags("[" + "x" * 50 + "] keep") == "[" + "x" * 50 + "] keep"   # too long to be a tag


def test_two_sentences_timings():
    s = sentences_from_alignment(align("Hi there. Bye now."))
    assert texts(s) == ["Hi there.", "Bye now."]
    assert s[0]["start_ms"] == 0 and s[0]["end_ms"] == 900
    assert s[1]["start_ms"] == 1000 and s[1]["end_ms"] == 1800


@pytest.mark.parametrize("line,expected", [
    ("Mr. Darcy called. He left.", ["Mr. Darcy called.", "He left."]),
    ("Mrs. Pike sat. Fine.", ["Mrs. Pike sat.", "Fine."]),
    ("J. Fairfax came. Yes.", ["J. Fairfax came.", "Yes."]),
    ("No. 5 Lombard Street. Nice.", ["No. 5 Lombard Street.", "Nice."]),
    ("No. It was not. Fine.", ["No.", "It was not.", "Fine."]),
    ("It cost 3.50 pounds. Dear.", ["It cost 3.50 pounds.", "Dear."]),
    ("Wait… what? Oh!", ["Wait…", "what?", "Oh!"]),
    ("Wait... what?", ["Wait...", "what?"]),
])
def test_abbreviations_and_terminators(line, expected):
    assert texts(sentences_from_alignment(align(line))) == expected


def test_no_terminator_yields_single_span():
    s = sentences_from_alignment(align("just words"))
    assert texts(s) == ["just words"] and s[0]["end_ms"] == 1000


def test_tags_skipped_and_do_not_shift_start():
    line = "[quietly] Hello there."
    s = sentences_from_alignment(align(line))
    assert texts(s) == ["Hello there."]
    # start belongs to "H", index 10 -> 1.0s (the space after the tag is leading whitespace)
    assert s[0]["start_ms"] == 1000


def test_trailing_text_after_last_terminator_flushed():
    assert texts(sentences_from_alignment(align("One. Two"))) == ["One.", "Two"]


def test_empty_alignment():
    assert sentences_from_alignment({"characters": [], "character_start_times_seconds": [],
                                     "character_end_times_seconds": []}) == []


# --- Clip --------------------------------------------------------------------

def test_clip_duration():
    assert Clip(pcm=b"\x00" * 48000, sample_rate=24000).duration_ms == 1000.0
    c = Clip(pcm=b"", sample_rate=24000)
    assert c.duration_ms == 0 and c.sentences == [] and c.unit == "characters"


# --- tags_enabled ------------------------------------------------------------

@pytest.mark.parametrize("el,expected", [
    ({"model": "eleven_v3"}, True),
    ({"model": "eleven_v4_turbo"}, True),
    ({"model": "eleven_multilingual_v2"}, False),
    ({"model": "eleven_v3", "audio_tags": False}, False),
    ({"model": "eleven_multilingual_v2", "audio_tags": True}, True),
    ({"model": "eleven_v3", "audio_tags": "auto"}, True),
    ({}, False),
])
def test_tags_enabled(el, expected):
    assert tags_enabled({"elevenlabs": el}) is expected


def test_tags_enabled_no_section():
    assert tags_enabled({}) is False


# --- ElevenLabs --------------------------------------------------------------

class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._d, self.text = status, data, text

    def json(self):
        return self._d


def good_payload(text="Hello. Bye."):
    return {"audio_base64": base64.b64encode(b"\x00\x01" * 4800).decode(),
            "alignment": align(text)}


@pytest.fixture
def el_cfg():
    return copy.deepcopy(cfgmod.load())


@pytest.fixture
def post(monkeypatch):
    calls = []
    responses = []

    def fake_post(url, params=None, json=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "json": json, "headers": headers})
        return responses.pop(0) if responses else Resp(200, good_payload())
    monkeypatch.setattr(tts.requests, "post", fake_post)
    monkeypatch.setattr(tts.time, "sleep", lambda s: calls.append(("sleep", s)))
    fake_post.calls, fake_post.responses = calls, responses
    return fake_post


def test_elevenlabs_init_defaults(el_cfg):
    e = ElevenLabs(el_cfg, "k")
    assert e.sample_rate == 24000 and e.model == "eleven_multilingual_v2"
    assert e.tags is False and e.context is True and e.max_chars == 2400


def test_resolve_voice(el_cfg):
    e = ElevenLabs(el_cfg, "k")
    lib = el_cfg["elevenlabs"]["voice_library"]
    assert e.resolve_voice("Daniel") == lib["Daniel"]
    assert e.resolve_voice("A" * 20) == "A" * 20
    with pytest.raises(ValueError, match="not in voices.yaml"):
        e.resolve_voice("Nobody")
    with pytest.raises(ValueError):
        e.resolve_voice("short")


def test_synthesize_builds_request(el_cfg, post):
    e = ElevenLabs(el_cfg, "secret")
    clip = e.synthesize("[quietly] Hello. Bye.", "VID", previous="p" * 600, following="f" * 600)
    c = post.calls[0]
    assert c["url"] == tts.ELEVEN_URL.format(voice="VID")
    assert c["params"] == {"output_format": "pcm_24000"}
    assert c["headers"]["xi-api-key"] == "secret"
    body = c["json"]
    assert body["text"] == "Hello. Bye."                  # tag stripped: model has no tags
    assert body["model_id"] == "eleven_multilingual_v2"
    assert body["apply_text_normalization"] == "auto"
    assert body["previous_text"] == "p" * 500             # tail
    assert body["next_text"] == "f" * 500                 # head
    assert "seed" not in body and "voice_settings" not in body
    assert clip.unit == "credits" and clip.billed_units == len("Hello. Bye.")
    assert texts(clip.sentences) == ["Hello.", "Bye."] and clip.sample_rate == 24000
    assert clip.pcm == b"\x00\x01" * 4800


def test_synthesize_previous_keeps_tail_following_keeps_head(el_cfg, post):
    e = ElevenLabs(el_cfg, "k")
    e.synthesize("x", "V", previous="a" * 500 + "TAIL", following="HEAD" + "b" * 500)
    body = post.calls[0]["json"]
    assert body["previous_text"].endswith("TAIL") and body["next_text"].startswith("HEAD")


def test_synthesize_v3_keeps_tags_drops_context(el_cfg, post):
    el_cfg["elevenlabs"]["model"] = "eleven_v3"
    e = ElevenLabs(el_cfg, "k")
    assert e.tags and not e.context
    e.synthesize("[quietly] Hi.", "V", previous="p", following="f")
    body = post.calls[0]["json"]
    assert body["text"] == "[quietly] Hi."
    assert "previous_text" not in body and "next_text" not in body


def test_synthesize_v4_takes_context_and_tags(el_cfg, post):
    el_cfg["elevenlabs"]["model"] = "eleven_v4_turbo"
    e = ElevenLabs(el_cfg, "k")
    assert e.tags and e.context
    clip = e.synthesize("abcd", "V", previous="p")
    assert "previous_text" in post.calls[0]["json"]
    assert clip.billed_units == 2                          # turbo bills half


def test_synthesize_seed_and_voice_settings(el_cfg, post):
    el_cfg["elevenlabs"]["seed"] = 0                       # 0 is a real seed
    el_cfg["elevenlabs"]["voice_settings"] = {"stability": 0.5}
    ElevenLabs(el_cfg, "k").synthesize("x", "V")
    body = post.calls[0]["json"]
    assert body["seed"] == 0 and body["voice_settings"] == {"stability": 0.5}


def test_synthesize_falls_back_to_normalized_alignment(el_cfg, post):
    p = good_payload()
    p["normalized_alignment"] = p.pop("alignment")
    post.responses.append(Resp(200, p))
    assert texts(ElevenLabs(el_cfg, "k").synthesize("x", "V").sentences) == ["Hello.", "Bye."]


def test_synthesize_without_alignment_raises(el_cfg, post):
    post.responses.append(Resp(200, {"audio_base64": ""}))
    with pytest.raises(RuntimeError, match="no alignment"):
        ElevenLabs(el_cfg, "k").synthesize("x", "V")


def test_post_retries_then_succeeds(el_cfg, post):
    post.responses.extend([Resp(429), Resp(503), Resp(200, good_payload())])
    ElevenLabs(el_cfg, "k").synthesize("x", "V")
    sleeps = [c[1] for c in post.calls if isinstance(c, tuple)]
    assert sleeps == [1, 2]
    assert len([c for c in post.calls if isinstance(c, dict)]) == 3


def test_post_non_retryable_status_raises_immediately(el_cfg, post):
    post.responses.append(Resp(401, text="bad key " * 100))
    with pytest.raises(RuntimeError, match=r"ElevenLabs 401: bad key") as e:
        ElevenLabs(el_cfg, "k").synthesize("x", "V")
    assert len(str(e.value)) < 400
    assert len(post.calls) == 1


def test_post_exhausts_attempts(el_cfg, post):
    post.responses.extend([Resp(500, text="e")] * 4)
    with pytest.raises(RuntimeError, match="ElevenLabs 500"):
        ElevenLabs(el_cfg, "k").synthesize("x", "V")
    assert len([c for c in post.calls if isinstance(c, dict)]) == 4


def test_estimate(el_cfg):
    e = ElevenLabs(el_cfg, "k").estimate(10_000)
    assert e == {"billed_units": 10000, "unit": "credits", "engine": "eleven_multilingual_v2",
                 "usd": round(10 * 0.22, 2)}
    el_cfg["elevenlabs"]["model"] = "eleven_flash_v2_5"
    assert ElevenLabs(el_cfg, "k").estimate(10_000)["billed_units"] == 5000
    el_cfg["elevenlabs"]["model"] = "unknown_model"
    assert ElevenLabs(el_cfg, "k").estimate(10)["billed_units"] == 10


def test_probe_sends_context_and_reports(el_cfg, post):
    e = ElevenLabs(el_cfg, "k")
    r = e.probe("Hello. Bye.", "Daniel")
    body = post.calls[0]["json"]
    assert body["previous_text"] and body["next_text"]
    assert post.calls[0]["url"].endswith(el_cfg["elevenlabs"]["voice_library"]["Daniel"] + "/with-timestamps")
    assert r["model"] == "eleven_multilingual_v2" and r["alignment"] is True
    assert r["tags"] is False and r["context"] is True
    assert r["billed"] == 11 and r["duration_ms"] == 200 and len(r["sentences"]) == 2


def test_list_voices(el_cfg, monkeypatch):
    seen = {}

    class R:
        def raise_for_status(self): seen["raised"] = True
        def json(self): return {"voices": [{"voice_id": "a"}]}
    monkeypatch.setattr(tts.requests, "get",
                        lambda url, headers, timeout: seen.update(url=url, headers=headers) or R())
    assert ElevenLabs(el_cfg, "kk").list_voices() == [{"voice_id": "a"}]
    assert seen["headers"] == {"xi-api-key": "kk"} and seen["raised"]


# --- Polly -------------------------------------------------------------------

class FakeStream:
    def __init__(self, b): self.b = b
    def read(self): return self.b


class FakePollyClient:
    def __init__(self):
        self.calls = []

    def synthesize_speech(self, **kw):
        self.calls.append(kw)
        if kw["OutputFormat"] == "pcm":
            return {"AudioStream": FakeStream(b"\x00\x00" * 16000)}      # 1 s at 16 kHz
        marks = [{"time": 0, "value": "One."}, {"time": 400, "value": "Two."}]
        return {"AudioStream": FakeStream("\n".join(json.dumps(m) for m in marks).encode() + b"\n")}


@pytest.fixture
def polly(monkeypatch):
    client = FakePollyClient()
    made = {}

    class Session:
        def __init__(self, profile_name=None):
            made["profile"] = profile_name
        def client(self, svc, region_name):
            made["svc"], made["region"] = svc, region_name
            return client
    fake = types.SimpleNamespace(Session=Session)
    monkeypatch.setitem(sys.modules, "boto3", fake)
    return client, made


def test_polly_synthesize(polly):
    client, made = polly
    cfg = copy.deepcopy(cfgmod.load())
    p = Polly(cfg)
    assert made == {"profile": None, "svc": "polly", "region": "us-east-1"}
    clip = p.synthesize("One. Two.", "Joanna", previous="x", following="y")
    assert clip.unit == "characters" and clip.billed_units == 9 and clip.sample_rate == 16000
    assert clip.sentences == [{"start_ms": 0, "end_ms": 400, "text": "One."},
                              {"start_ms": 400, "end_ms": 1000, "text": "Two."}]
    assert [c["OutputFormat"] for c in client.calls] == ["pcm", "json"]
    assert client.calls[0]["SampleRate"] == "16000" and client.calls[0]["Engine"] == "generative"
    assert client.calls[1]["SpeechMarkTypes"] == ["sentence"]
    assert p.resolve_voice("Joanna") == "Joanna"


def test_polly_profile(polly):
    _, made = polly
    cfg = copy.deepcopy(cfgmod.load())
    cfg["polly"]["profile"] = "dev"
    Polly(cfg)
    assert made["profile"] == "dev"


def test_polly_estimate(polly):
    cfg = copy.deepcopy(cfgmod.load())
    assert Polly(cfg).estimate(1_000_000) == {"billed_units": 1000000, "unit": "characters",
                                              "engine": "generative", "usd": 30.0}
    cfg["polly"]["engine"] = "mystery"
    assert Polly(cfg).estimate(5)["usd"] == 0


# --- build -------------------------------------------------------------------

def test_build_elevenlabs_default(el_cfg):
    p = tts.build(el_cfg)
    assert isinstance(p, ElevenLabs) and p.key == "test-not-a-real-key"


def test_build_requires_key(el_cfg, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY is not set"):
        tts.build(el_cfg)


def test_build_polly_and_unknown(polly, el_cfg):
    el_cfg["tts_provider"] = "polly"
    assert isinstance(tts.build(el_cfg), Polly)
    el_cfg["tts_provider"] = "robot"
    with pytest.raises(ValueError, match="unknown tts_provider 'robot'"):
        tts.build(el_cfg)
