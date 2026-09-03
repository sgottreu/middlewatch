"""Text-to-speech providers.

Both providers return the same thing: PCM audio plus sentence-level timings. The
timings are the point — they drive the subtitle file and the image cut points, so
every provider has to produce them or the rest of the pipeline has nothing to
work with.

The two get there differently. Polly returns sentence marks directly, from a
second billed call. ElevenLabs returns character-level alignment in the same
response as the audio, which is finer-grained and half the calls, but means the
sentence boundaries have to be derived here.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

import requests

# Titles and abbreviations whose full stop does not end a sentence. Without this
# guard, "Mr. Darcy called" becomes two subtitle cues and an image cut lands
# mid-name. Regency prose is unusually dense with these.
ABBREVIATIONS = {
    "mr", "mrs", "ms", "miss", "dr", "st", "capt", "lt", "col", "gen", "maj",
    "sgt", "rev", "hon", "sr", "jr", "esq", "prof", "vs", "etc", "viz", "ibid",
    "inc", "ltd", "co",
}

# Ambiguous: these abbreviate only when a number follows. "No. 5 Lombard Street"
# is one sentence; "No." on its own is a complete line of dialogue, which in this
# genre is extremely common.
NUMERIC_ABBREVIATIONS = {"no", "vol", "fig", "ch", "pp", "p", "art", "sec"}

TERMINATORS = ".!?\u2026"


@dataclass
class Clip:
    """One synthesized span of text."""

    pcm: bytes
    sample_rate: int
    sentences: list[dict] = field(default_factory=list)  # start_ms/end_ms/text, clip-relative
    billed_units: int = 0
    unit: str = "characters"

    @property
    def duration_ms(self) -> float:
        return len(self.pcm) / 2 / self.sample_rate * 1000  # 16-bit mono


class Provider(Protocol):
    name: str
    sample_rate: int
    max_chars: int

    def synthesize(
        self, text: str, voice: str, previous: str | None, following: str | None
    ) -> Clip: ...

    def resolve_voice(self, name: str) -> str: ...

    def estimate(self, chars: int) -> dict: ...


# --------------------------------------------------------------------------- #
# Sentence boundaries from character alignment
# --------------------------------------------------------------------------- #


def sentences_from_alignment(alignment: dict) -> list[dict]:
    """Collapse per-character timings into per-sentence spans.

    Walks the characters the API actually returned rather than the text we sent,
    since normalization can change them and the timings belong to the former.
    """
    chars = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]

    out: list[dict] = []
    buf: list[str] = []
    start: float | None = None

    def flush(end_time: float) -> None:
        nonlocal buf, start
        text = "".join(buf).strip()
        if text and start is not None:
            out.append({"start_ms": round(start * 1000), "end_ms": round(end_time * 1000),
                        "text": text})
        buf, start = [], None

    for i, ch in enumerate(chars):
        if start is None and not ch.isspace():
            start = starts[i]
        buf.append(ch)

        if ch not in TERMINATORS:
            continue
        if ch == "." and _is_abbreviation("".join(buf), chars, i):
            continue

        # A terminator only ends a sentence if what follows is whitespace or the
        # end of the clip. Otherwise it's a decimal, an ellipsis mid-run, or an
        # initial like "J.R."
        nxt = chars[i + 1] if i + 1 < len(chars) else " "
        if nxt.isspace() or i + 1 == len(chars):
            flush(ends[i])

    if buf:
        flush(ends[-1] if ends else 0.0)

    if not out and chars:
        out = [{"start_ms": round(starts[0] * 1000), "end_ms": round(ends[-1] * 1000),
                "text": "".join(chars).strip()}]
    return out


def _is_abbreviation(buffer: str, chars: list[str], i: int) -> bool:
    """True when the full stop just consumed belongs to a title or an initial."""
    word = re.split(r"[\s\u201c\u201d\"(]", buffer.strip())[-1].rstrip(".").lower()
    if word in ABBREVIATIONS:
        return True
    if len(word) == 1 and word.isalpha():
        return True  # "J." in "J. Fairfax"
    if word in NUMERIC_ABBREVIATIONS:
        nxt = "".join(chars[i + 1 : i + 4]).strip()
        return bool(nxt) and nxt[0].isdigit()
    return False


# --------------------------------------------------------------------------- #
# ElevenLabs
# --------------------------------------------------------------------------- #

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"

# Credit cost per character. TTS bills 1 credit per character on the standard
# models and half that on the turbo/flash line. Verify against your plan before
# trusting the dry run's dollar figure.
CREDIT_RATE = {
    "eleven_multilingual_v2": 1.0,
    "eleven_v3": 1.0,
    "eleven_turbo_v2_5": 0.5,
    "eleven_flash_v2_5": 0.5,
}


class ElevenLabs:
    name = "elevenlabs"
    max_chars = 2400

    def __init__(self, cfg: dict, api_key: str):
        self.cfg = cfg["elevenlabs"]
        self.key = api_key
        self.model = self.cfg["model"]
        # 44.1kHz PCM needs a Pro plan; 24kHz doesn't and is plenty for narration
        # over stills. Keep PCM rather than mp3 so the stitch stays lossless.
        self.sample_rate = int(self.cfg["output_format"].split("_")[1])
        self.usd_per_1k_credits = self.cfg.get("usd_per_1k_credits", 0.0)

    def resolve_voice(self, name: str) -> str:
        """Genre files name voices; the library maps names to IDs.

        Keeping the ID in one place means a deprecated voice is a one-line config
        fix rather than an edit to every genre file.
        """
        library = self.cfg.get("voice_library", {})
        if name in library:
            return library[name]
        if re.fullmatch(r"[A-Za-z0-9]{20}", name):
            return name  # already an ID
        raise ValueError(
            f"voice {name!r} is not in elevenlabs.voice_library and is not a "
            f"voice ID. Run `cli voices` to list your account's voices."
        )

    def synthesize(self, text, voice, previous=None, following=None) -> Clip:
        body = {
            "text": text,
            "model_id": self.model,
            "apply_text_normalization": self.cfg.get("text_normalization", "auto"),
        }
        # Continuity across segment joins. Each narrator/dialogue switch is a
        # separate request, and without this the model restarts its prosody cold
        # at every one — the join is audible as a reset in pace and pitch.
        if previous:
            body["previous_text"] = previous[-500:]
        if following:
            body["next_text"] = following[:500]
        if self.cfg.get("seed") is not None:
            body["seed"] = self.cfg["seed"]
        if self.cfg.get("voice_settings"):
            body["voice_settings"] = self.cfg["voice_settings"]

        data = self._post(
            ELEVEN_URL.format(voice=voice),
            params={"output_format": self.cfg["output_format"]},
            json_body=body,
        )

        alignment = data.get("alignment") or data.get("normalized_alignment")
        if not alignment:
            raise RuntimeError("ElevenLabs returned no alignment; timings are required")

        return Clip(
            pcm=base64.b64decode(data["audio_base64"]),
            sample_rate=self.sample_rate,
            sentences=sentences_from_alignment(alignment),
            billed_units=round(len(text) * CREDIT_RATE.get(self.model, 1.0)),
            unit="credits",
        )

    def _post(self, url: str, params: dict, json_body: dict, attempts: int = 4) -> dict:
        last = None
        for i in range(attempts):
            r = requests.post(
                url,
                params=params,
                json=json_body,
                headers={"xi-api-key": self.key, "Content-Type": "application/json"},
                timeout=180,
            )
            if r.status_code == 200:
                return r.json()
            # 429 is a concurrency cap, not a quota wall — backing off clears it.
            if r.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(2 ** i)
                last = r
                continue
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
        raise RuntimeError(f"ElevenLabs failed after {attempts} attempts: {last}")

    def estimate(self, chars: int) -> dict:
        credits = round(chars * CREDIT_RATE.get(self.model, 1.0))
        return {
            "billed_units": credits,
            "unit": "credits",
            "engine": self.model,
            "usd": round(credits / 1000 * self.usd_per_1k_credits, 2),
        }

    def list_voices(self) -> list[dict]:
        r = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": self.key},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("voices", [])


# --------------------------------------------------------------------------- #
# Polly (kept working; ElevenLabs is the default)
# --------------------------------------------------------------------------- #

POLLY_RATES = {"standard": 4.0, "neural": 16.0, "generative": 30.0, "long-form": 100.0}


class Polly:
    name = "polly"
    max_chars = 2500

    def __init__(self, cfg: dict):
        import boto3

        p = cfg["polly"]
        self.engine = p["engine"]
        self.sample_rate = p["sample_rate"]
        session = boto3.Session(profile_name=p["profile"]) if p.get("profile") else boto3.Session()
        self.client = session.client("polly", region_name=p["region"])

    def resolve_voice(self, name: str) -> str:
        return name  # Polly voices are named, not IDs

    def synthesize(self, text, voice, previous=None, following=None) -> Clip:
        # previous/following are ignored: Polly has no continuity parameter, which
        # is the main reason its segment joins sound flatter.
        audio = self.client.synthesize_speech(
            Text=text, VoiceId=voice, Engine=self.engine,
            OutputFormat="pcm", SampleRate=str(self.sample_rate),
        )["AudioStream"].read()

        raw = self.client.synthesize_speech(
            Text=text, VoiceId=voice, Engine=self.engine,
            OutputFormat="json", SpeechMarkTypes=["sentence"],
        )["AudioStream"].read()
        marks = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]

        duration = len(audio) / 2 / self.sample_rate * 1000
        sentences = [{"start_ms": m["time"], "text": m["value"]} for m in marks]
        for i, s in enumerate(sentences):
            s["end_ms"] = sentences[i + 1]["start_ms"] if i + 1 < len(sentences) else round(duration)

        return Clip(pcm=audio, sample_rate=self.sample_rate, sentences=sentences,
                    billed_units=len(text), unit="characters")

    def estimate(self, chars: int) -> dict:
        return {
            "billed_units": chars,
            "unit": "characters",
            "engine": self.engine,
            "usd": round(chars / 1_000_000 * POLLY_RATES.get(self.engine, 0), 2),
        }


def build(cfg: dict) -> Provider:
    provider = cfg.get("tts_provider", "elevenlabs")
    if provider == "elevenlabs":
        from .config import require_env

        return ElevenLabs(cfg, require_env("ELEVENLABS_API_KEY"))
    if provider == "polly":
        return Polly(cfg)
    raise ValueError(f"unknown tts_provider {provider!r}; use elevenlabs or polly")
