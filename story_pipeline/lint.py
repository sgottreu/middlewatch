"""Deterministic AI-tell checks.

Prompt instructions alone don't hold: a model told not to write "underscoring"
writes it anyway a few thousand tokens later. Most of these patterns are regular
expressions, so they get caught here for free and turned into precise fixes
before the paid editor pass ever runs.

What this file deliberately does NOT do is judge whether the prose is any good.
Passing the linter means the obvious machine signatures are gone. The disease —
generic writing where specific writing belongs — is the editor's job, and the
linter is worthless against it. See prompts/ai-tells.md.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass


@dataclass
class Finding:
    rule: str
    severity: str  # "error" blocks a pass; "warn" is advisory
    quote: str
    note: str
    segment: int = -1

    def as_fix(self) -> str:
        where = f"segment {self.segment}: " if self.segment >= 0 else ""
        return f"{where}\u201c{self.quote}\u201d \u2014 {self.note}"


# --------------------------------------------------------------------------- #
# Word and phrase lists
# --------------------------------------------------------------------------- #

# Overused to the point of signature. Concrete senses are exempted below.
AI_VOCAB = [
    "align with", "boasts", "bolster", "bolstered", "crucial", "deep dive",
    "delve", "delved", "delving", "enduring", "enhance", "enhanced", "enhancing",
    "foster", "fostered", "fostering", "garner", "garnered", "interplay",
    "intricate", "intricacies", "meticulous", "meticulously", "pivotal",
    "robust", "showcase", "showcased", "showcasing", "tapestry", "testament",
    "vibrant",
]

# Participles that interpret rather than act. The distinction matters: "sewing"
# is an action, "underscoring" is the narrator explaining the last sentence.
#
# Two tiers, because the comma is not reliable. "her attention to the guests
# underscoring her commitment" is the same tell with no comma in it. The first
# tier is analytic in any position; the second has honest uses ("the clerk
# marking the ledger") and only counts when hung off the end of a clause.
ANALYTIC_ALWAYS = [
    "underscoring", "highlighting", "emphasizing", "emphasising", "showcasing",
    "symbolizing", "symbolising", "cementing", "solidifying", "exemplifying",
    "reinforcing", "embodying", "epitomizing", "epitomising", "encapsulating",
    "setting the stage",
]
ANALYTIC_AFTER_COMMA = [
    "reflecting", "signaling", "signalling", "contributing to", "cultivating",
    "fostering", "encompassing", "enhancing", "demonstrating", "illustrating",
    "marking", "ensuring", "revealing the", "affirming", "capturing the",
    "cementing her", "speaking to the",
]

COPULA_DODGES = [
    "serves as", "served as", "serving as", "stands as", "stood as",
    "functions as", "functioned as", "operates as", "represents a",
    "represented a", "marks a", "marked a", "boasts a", "features a",
    "featured a", "offers a", "offered a", "maintains a",
]

SIGNIFICANCE = [
    "a testament to", "stands as a", "a turning point", "a pivotal moment",
    "a defining moment", "a broader shift", "an indelible mark", "a crucial role",
    "a vital role", "a significant role", "a lasting legacy", "the broader context",
    "a profound impact", "would forever change", "little did she know",
    "little did he know", "in that moment", "little knowing",
]

PROMOTIONAL = [
    "nestled", "in the heart of", "renowned", "rich history", "rich tapestry",
    "rich tradition", "diverse array", "natural beauty", "groundbreaking",
    "exemplifies", "commitment to", "a myriad of", "a testament",
]

MODERN_REGISTER = [
    "closure", "boundaries", "processing", "toxic", "validate", "validated",
    "self-care", "unpack", "hold space", "emotional labor", "emotional labour",
    "trauma", "triggered", "coping mechanism", "red flag", "gaslighting",
    "reach out", "check in on", "space to breathe", "mental health",
    "a mixture of", "a whirlwind of emotions", "overthinking",
]

# Words above that are innocent in a concrete sense; skip when nearby.
CONTEXT_EXEMPT = {
    "boasts": ["boasts of", "boasted of"],
    "marking": ["marking the linen", "marking paper", "marking his place"],
    "testament": ["last will and testament", "new testament", "old testament"],
    "robust": ["robust health", "robust constitution"],
}

NEGATIVE_PARALLELISM = [
    r"\bnot (?:just|only|merely|simply)\b[^.!?]{2,80}?\bbut\b",
    r"\b(?:was|is|were|are|it|this|that)(?:'s| was| is)? not [^.!?]{2,60}?[;,\u2014]\s*(?:it|but|rather|she|he) (?:was|is|constitutes|represents)\b",
    r"\bno [a-z]+, no [a-z]+,\s*(?:just|only)\b",
]


def _words(text: str) -> int:
    return len(text.split())


def _clip(sent: str, limit: int = 140) -> str:
    return sent if len(sent) <= limit else sent[:limit].rstrip() + "..."


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?\u201d])\s+", text.strip())
    return [p for p in parts if p.strip()]


# A sentence that coordinates clause after clause with a comma and a conjunction.
# Period prose does this on purpose and does it well, so the rule is not "never" —
# it is a count, checked against length, because what a reader takes at a glance a
# listener has to hold to the end.
CLAUSE_JOIN = re.compile(r",\s+(?:and|but|or|nor|yet|so)\s+", re.I)

# Measured against the first two stories: 769 sentences, median 10 words. Over 40
# is 2.9% of them and 1.4 a chapter; two joins past 25 words is 2.9 a chapter.
# Both are rare enough to mean something when they fire.
LONG_SENTENCE_WORDS = 40
CHAIN_JOINS = 2
CHAIN_WORDS = 25


def _quote(text: str, start: int, end: int, pad: int = 40) -> str:
    lo, hi = max(0, start - pad), min(len(text), end + pad)
    frag = text[lo:hi].replace("\n", " ").strip()
    return ("..." if lo else "") + frag + ("..." if hi < len(text) else "")


def _exempt(term: str, text: str, pos: int) -> bool:
    window = text[max(0, pos - 30) : pos + 40].lower()
    return any(phrase in window for phrase in CONTEXT_EXEMPT.get(term, []))


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def _phrase_rule(text: str, terms: list[str], rule: str, note: str, seg: int) -> list[Finding]:
    out = []
    for term in terms:
        pattern = r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b"
        for m in re.finditer(pattern, text, re.I):
            if _exempt(term, text, m.start()):
                continue
            out.append(Finding(rule, "error", _quote(text, *m.span()), note, seg))
    return out


def lint_segment(text: str, seg: int) -> list[Finding]:
    f: list[Finding] = []

    f += _phrase_rule(text, AI_VOCAB, "ai_vocab", seg=seg,
                      note="AI-signature word. Don't swap a synonym in; cut the "
                           "generality and put something particular there instead.")

    note = ("Clause explaining what the sentence already meant. Delete it and end "
            "at the fact.")
    for term in ANALYTIC_ALWAYS:
        for m in re.finditer(r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b", text, re.I):
            f.append(Finding("analytic_participle", "error", _quote(text, *m.span()), note, seg))
    for term in ANALYTIC_AFTER_COMMA:
        pat = r"[,;]\s+" + re.escape(term).replace(r"\ ", r"\s+") + r"\b"
        for m in re.finditer(pat, text, re.I):
            f.append(Finding("analytic_participle", "error", _quote(text, *m.span()), note, seg))

    f += _phrase_rule(text, COPULA_DODGES, "copula_dodge", seg=seg,
                      note="Use a plain copula: was, had, is.")
    f += _phrase_rule(text, SIGNIFICANCE, "manufactured_significance", seg=seg,
                      note="The narrator is announcing importance instead of earning it. "
                           "Show the thing and let the reader judge.")
    f += _phrase_rule(text, PROMOTIONAL, "promotional", seg=seg,
                      note="Travel-brochure register. Describe what is actually there.")
    f += _phrase_rule(text, MODERN_REGISTER, "modern_register", seg=seg,
                      note="Modern therapeutic vocabulary; wrong by two centuries.")

    for pattern in NEGATIVE_PARALLELISM:
        for m in re.finditer(pattern, text, re.I):
            f.append(Finding(
                "negative_parallelism", "warn", _quote(text, *m.span()),
                "Negative parallelism. One per story at most.", seg))

    for m in re.finditer(r"\s[\u2014\u2013]\s", text):
        f.append(Finding(
            "spaced_em_dash", "warn", _quote(text, *m.span(), pad=30),
            "Spaced dash reads as modern punch-up. Close it up or use a comma.", seg))

    return f


# --------------------------------------------------------------------------- #
# Whole-chapter shape
# --------------------------------------------------------------------------- #


def lint_shape(full_text: str) -> list[Finding]:
    f: list[Finding] = []
    sentences = _sentences(full_text)
    lengths = [_words(s) for s in sentences if _words(s) > 2]

    if len(lengths) >= 8:
        cv = statistics.pstdev(lengths) / (statistics.mean(lengths) or 1)
        if cv < 0.42:
            f.append(Finding(
                "uniform_cadence", "warn",
                f"{len(lengths)} sentences, mean {statistics.mean(lengths):.0f} words, "
                f"variation {cv:.2f}",
                "Sentence lengths are too even to read as human. Break at least three "
                "of them into something under seven words."))

    for sent in sentences:
        words, joins = _words(sent), len(CLAUSE_JOIN.findall(sent))
        # Two findings rather than one, because they are different faults with
        # different repairs: sprawl is cut, a chain is split.
        if words > LONG_SENTENCE_WORDS:
            f.append(Finding(
                "long_sentence", "warn", _clip(sent),
                f"{words} words in one sentence. A listener cannot re-read it. "
                f"Split it, or cut it to under {LONG_SENTENCE_WORDS}."))
        elif joins >= CHAIN_JOINS and words > CHAIN_WORDS:
            f.append(Finding(
                "clause_chain", "warn", _clip(sent),
                f"{joins + 1} clauses coordinated in {words} words. Heard once, "
                f"the last one lands with the subject of the first already gone. "
                f"Split after the first clause, or make every member the same "
                f"grammatical shape so the ear can follow the pattern."))

    triples = re.findall(r"\b(\w+), (\w+),? and (\w+)\b", full_text)
    per_1k = len(triples) / max(_words(full_text) / 1000, 0.5)
    if per_1k > 4:
        f.append(Finding(
            "rule_of_three", "warn",
            f"{len(triples)} three-item lists (e.g. {', '.join(triples[0])})",
            "Overused triples. Convert some to two items or four."))

    return f


def lint_chapter(chapter: dict) -> list[Finding]:
    findings: list[Finding] = []
    for i, seg in enumerate(chapter.get("segments", [])):
        findings += lint_segment(seg["text"], i)
    findings += lint_shape(" ".join(s["text"] for s in chapter.get("segments", [])))
    return findings


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


def summarize(findings: list[Finding]) -> dict:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.rule] = counts.get(f.rule, 0) + 1
    return {
        "total": len(findings),
        "errors": len(errors(findings)),
        "by_rule": counts,
        "findings": [
            {"rule": f.rule, "severity": f.severity, "segment": f.segment,
             "quote": f.quote, "note": f.note}
            for f in findings
        ],
    }


def to_fixes(findings: list[Finding], limit: int = 12) -> list[str]:
    """Errors first, then warnings, deduplicated by quote."""
    ordered = errors(findings) + [f for f in findings if f.severity == "warn"]
    seen, out = set(), []
    for f in ordered:
        if f.quote in seen:
            continue
        seen.add(f.quote)
        out.append(f.as_fix())
        if len(out) >= limit:
            break
    return out
