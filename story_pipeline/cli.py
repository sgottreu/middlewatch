"""CLI. Stages run independently so a failure costs one stage, not a whole story."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config as cfgmod
from . import bibles
from . import calibration
from . import changelog
from . import genres
from . import history
from . import tts
from . import lint as linter
from . import summary
from .review import serve as serve_review
from .agents import actor, designer, director, text
from .bundle import Bundle, STAGES, real_answer


def _bundle(args) -> Bundle:
    return Bundle.open(Path(args.story))


def _progress(**fields) -> None:
    """Emit one progress marker, when something is reading this as a job.

    The review UI runs these commands as detached processes and follows the log,
    so progress has to survive a process boundary. A marker line keeps that
    contract explicit — the alternative, parsing the human output, would make
    every print() in the pipeline load-bearing. Off unless MW_PROGRESS is set,
    so a terminal run reads the way it always did.
    """
    if os.environ.get("MW_PROGRESS"):
        print("::progress " + json.dumps(fields), flush=True)


def _show_outline(b, outline):
    print(f"\n{outline['title']}")
    if outline.get("series"):
        print(f"{outline['series']}")
    print(f"{outline['genre']} / {outline.get('subgenre', '?')}"
          f"   tropes: {', '.join(outline.get('tropes', []))}")
    if real_answer(outline.get("bible_conflict")):
        print(f"\n! Your notes contradicted the series bible: "
              f"{real_answer(outline['bible_conflict'])}")
    print(f"\n{outline['logline']}")
    if outline.get("promise"):
        print(f"Promise: {outline['promise']}")
    print(f"Setting: {outline['setting']}")
    if real_answer(outline.get("notes_conflict")):
        print(f"\n! Your notes conflicted with the content boundaries: "
              f"{real_answer(outline['notes_conflict'])}")
    print("\nCast:")
    for c in outline["cast"]:
        tag = " [recurring]" if c.get("recurring") else ""
        print(f"  {c['name']}, {c.get('age', '?')} ({c['role']}){tag} — {c['want']}")

    # The structural age check keys off `role`, so a minor mislabelled
    # "supporting" gets through it. Surface every under-18 here instead; the
    # review gate is the backstop that check can't be.
    minors = [c for c in outline["cast"] if isinstance(c.get("age"), int) and c["age"] < 18]
    if minors:
        print("\n! Under 18 in this cast: "
              + ", ".join(f"{c['name']} ({c['age']}, {c['role']})" for c in minors))
        print("  Confirm they are peripheral and carry no romantic charge before approving.")
    print("\nChapters:")
    for ch in outline["chapters"]:
        print(f"  {ch['n']}. {ch['title']}  ({ch['target_words']}w)")
        for beat in ch["beats"]:
            print(f"       - {beat}")

    # The planned length, before a word is written. This is the last point where
    # the shape of the story is free to change, so it is worth seeing here rather
    # than discovering it at record --dry-run.
    planned = sum(ch["target_words"] for ch in outline["chapters"])
    target = text.length_spec(outline.get("length_min"))
    print(f"\nPlanned: {planned:,} words across {len(outline['chapters'])} chapters "
          f"~{planned / text.WPM:.0f} min (target {target['minutes']})")

    # Words per beat is the tell for a story that is long rather than big. It is
    # free to look at here and expensive to discover after narration.
    beats = sum(len(ch["beats"]) for ch in outline["chapters"])
    per_beat = planned / beats if beats else 0
    flag = "  <- thin; the prose will stretch to fill this" if per_beat > 150 else ""
    print(f"Density: {beats} beats, {per_beat:.0f} words per beat "
          f"(asked for {target['beats_min']}-{target['beats_max']} per chapter){flag}")
    print(f"\nBundle: {b.root}")
    print("\nReview it, then:")
    print(f"  python3 -m story_pipeline.cli revise  {b.root} \"make the sister older, cut the ball\"")
    print(f"  python3 -m story_pipeline.cli approve {b.root}")


def _read_notes(args) -> str | None:
    if getattr(args, "notes_file", None):
        return Path(args.notes_file).read_text().strip()
    return getattr(args, "notes", None)


def cmd_bibles(args, cfg):
    found = bibles.available(cfg["bibles_dir"])
    if not found:
        print(f"No bibles in {cfg['bibles_dir']}/.")
        print("Create one:  python3 -m story_pipeline.cli bible new amberlight --genre regency")
        return
    for name in found:
        bl = bibles.load(name, cfg["bibles_dir"])
        print(f"\n{name}  ({bl.series})  [{bl.genre}]")
        print(f"  canon facts:    {len(bl.canon)}")
        print(f"  recurring cast: {len(bl.recurring_cast)}")
        for c in bl.recurring_cast:
            face = "portrait" if bl.portrait(c["name"]) else "no portrait yet"
            print(f"    - {c['name']}"
                  + (f", {c['age']}" if c.get("age") else "")
                  + (f" [voice {c['voice']}]" if c.get("voice") else "")
                  + f"  ({face})")


def cmd_bible_new(args, cfg):
    path = bibles.scaffold(args.name, args.genre, args.series, cfg["bibles_dir"],
                           example=args.example)
    author = changelog.resolve_author(getattr(args, "author", None), cfg)
    log = changelog.append(
        path,
        f"Created from the {'worked example' if args.example else 'blank'} scaffold, "
        f"genre {args.genre}.",
        author=author, kind="created",
        series=args.series or args.name,
    )
    print(f"Wrote {path}")
    print(f"      {log}")
    if args.example:
        print("Filled in as a worked example. Edit it — the content is a shape, "
              "not a series.")
    else:
        print("Fill in canon and recurring_cast, then:")
    print(f"  python3 -m story_pipeline.cli bible check {args.name}")
    print(f"  python3 -m story_pipeline.cli new --bible {args.name}")


def cmd_bible_check(args, cfg):
    """Validate bibles without calling a model. Free, and the same error-level
    checks the ideator runs before it spends anything."""
    names = [args.name] if args.name else bibles.available(cfg["bibles_dir"])
    if not names:
        print(f"No bibles in {cfg['bibles_dir']}/.")
        return

    worst = 0
    for name in names:
        path = Path(cfg["bibles_dir"]) / f"{name}.md"
        if not path.exists():
            sys.exit(f"no bible {name!r} in {cfg['bibles_dir']}/")
        problems = bibles.validate(path, cfg)
        errors = bibles.fatal(problems)
        warnings = [p for p in problems if not p.fatal]

        # Not a validation failure — the bible is fine. But an edit with no entry
        # behind it is exactly what the log exists to catch, so it is said here
        # rather than nowhere.
        stale = changelog.is_stale(path)
        note = ""
        if stale:
            last = changelog.entries(path)[0]
            note = (f"  (edited since the last changelog entry, "
                    f"{last.when} by {last.author})")
        elif not changelog.entries(path):
            note = "  (no changelog yet)"

        if not problems:
            print(f"{name}: ok{note}")
            continue
        print(f"\n{name}  —  {len(errors)} error(s), {len(warnings)} warning(s){note}")
        for p in problems:
            print(p)
        worst = max(worst, 2 if errors else 1)

    if worst == 2:
        print("\nErrors will stop `new --bible` before it spends anything.")
        sys.exit(1)
    if worst == 1:
        print("\nWarnings only. These won't stop a run, but they will drift.")


def cmd_bible_log(args, cfg):
    """Append an entry, or show the history."""
    path = Path(cfg["bibles_dir"]) / f"{args.name}.md"
    if not path.exists():
        sys.exit(f"no bible {args.name!r} in {cfg['bibles_dir']}/")

    if not args.message:
        found = changelog.entries(path)
        if not found:
            print(f"No changelog for {args.name} yet.")
            print(f'  python3 -m story_pipeline.cli bible log {args.name} '
                  f'-m "what changed"')
            return
        for e in found[: args.limit]:
            print(f"{e.when}  {e.author}  [{e.kind}]  sha={e.sha}")
            for line in e.summary.splitlines():
                if line.strip():
                    print(f"    {line}")
        if changelog.is_stale(path):
            print("\n! The bible has been edited since this entry was written.")
        return

    bible = bibles.load(args.name, cfg["bibles_dir"])
    author = changelog.resolve_author(args.author, cfg)
    log = changelog.append(path, args.message, author=author,
                           kind=args.kind, series=bible.series)
    print(f"Logged to {log} as {author}.")


def cmd_history(args, cfg):
    """What the channel has repeated, and how the next outline is weighted.

    Free — reads the bundles on disk, calls nothing.
    """
    h = history.scan(cfg["stories_dir"], bible=args.bible)
    scope = f" in {args.bible}" if args.bible else ""
    if not h.stories:
        print(f"No approved stories{scope} yet.")
        print("Only approved outlines count — rejecting one at the review gate")
        print("costs nothing and leaves no trace here.")
        return

    print(f"{h.stories} approved stor{'y' if h.stories == 1 else 'ies'}{scope}\n")

    g = genres.load(args.genre) if args.genre else None
    if g:
        print(f"Tropes — {g.name} ({len(g.tropes)} available, "
              f"{cfg['trope_sample']} shown per run)")
        print(f"  {'trope':<34} {'used':>4}  {'weight':>6}  odds vs unused")
        print("  " + "-" * 66)
        for t in sorted(g.tropes, key=lambda t: (-h.tropes.get(t["name"].lower(), 0),
                                                 t["name"])):
            n = h.tropes.get(t["name"].lower(), 0)
            w = h.weight(t["name"])
            bar = "#" * n
            print(f"  {t['name'][:34]:<34} {n:>4}  {w:>6.2f}  {bar}")
    else:
        print("Tropes used")
        for t, n in h.tropes.most_common():
            print(f"  {n:>3}x  {t}")
        print("\n(pass --genre to see the full menu and the weights applied)")

    if h.protagonists:
        print("\nProtagonists")
        for n, c in h.protagonists.most_common(8):
            print(f"  {c:>3}x  {n}")
    if h.subgenres:
        print("\nSubgenres")
        for s, c in h.subgenres.most_common():
            print(f"  {c:>3}x  {s}")

    reused = [(n, c) for n, c in h.invented_cast.most_common() if c > 1]
    if reused:
        print("\nInvented names used more than once "
              "(not in a bible, so these are unrelated people sharing a name)")
        for n, c in reused:
            print(f"  {c:>3}x  {n}")

    print("\nThe next outline is sampled against these weights, and the ideator")
    print("is told what has already been done. Nothing is excluded — a trope used")
    print("three times is about a quarter as likely to be offered, not banned.")


def cmd_review(args, cfg):
    """Open the review queue in a browser. Free — it reads bundles and writes
    stage state, and calls no model."""
    serve_review(cfg, port=args.port, open_browser=not args.no_open,
                 reload=args.reload)


def cmd_genres(args, cfg):
    for name in genres.available():
        g = genres.load(name)
        print(f"\n{name}  ({g.meta.get('label', name)})")
        print(f"  subgenres: {', '.join(g.subgenres)}")
        print(f"  tropes:    {len(g.tropes)} available")
        if args.tropes:
            for t in g.tropes:
                print(f"    - {t['name']}: {t['note']}")


def cmd_new(args, cfg):
    b = text.ideate(cfg, cfg["stories_dir"], args.genre, args.subgenre,
                    _read_notes(args), args.bible, length_min=args.length)
    summary.write(b)
    _show_outline(b, b.outline())


def cmd_revise(args, cfg):
    b = _bundle(args)
    outline = text.revise_outline(cfg, b, args.feedback)
    summary.write(b)
    _show_outline(b, outline)


def cmd_approve(args, cfg):
    b = _bundle(args)
    if args.chapters or args.chapter:
        picked = args.chapter or None
        approved = text.approve_chapters(b, picked)
        summary.write(b)
        written = [n for n in b.chapter_numbers() if b.chapter_json(n).exists()]
        left = [n for n in written if n not in approved]
        if left:
            print(f"Approved {len(approved)} of {len(written)}. "
                  f"Still to read: {', '.join(str(n) for n in left)}")
            print(f"  python3 -m story_pipeline.cli approve {b.root} --chapter "
                  f"{left[0]}")
        else:
            print("All chapters approved. Next: "
                  f"python3 -m story_pipeline.cli record {b.root} --dry-run")
        return
    text.approve(b)
    summary.write(b)
    print(f"Approved. Next: python3 -m story_pipeline.cli write {b.root}")


def cmd_write(args, cfg):
    b = _bundle(args)
    if b.stage_status("ideate") != "approved" and not args.force:
        sys.exit(
            "This outline hasn't been approved. Review it, then either\n"
            f"  python3 -m story_pipeline.cli revise  {b.root} \"what to change\"\n"
            f"  python3 -m story_pipeline.cli approve {b.root}\n"
            "or pass --force to skip the gate."
        )
    def progress(n, total, review):
        _progress(event="chapter", n=n, total=total,
                  ok=bool(review.get("pass")), score=review.get("score"),
                  exhausted=bool(review.get("exhausted")))

    try:
        reviews = text.run_text_stages(cfg, b, restart=args.restart,
                                       on_chapter=progress)
    except Exception:
        # Same reason as the UI path: `run_text_stages` sets write=running on
        # entry, so a crash would leave the stage stuck and the story neither
        # finished nor startable. Completed chapters stay on disk.
        b.set_stage("write", "pending")
        summary.write(b)
        raise
    summary.write(b)
    unpassed = [n for n, r in reviews.items() if not r.get("pass")]
    print(f"\n{b.word_count()} words across {len(reviews)} chapters")
    if unpassed:
        print(f"Chapters still failing review: {unpassed}")
        print("Read reviews/NN.json, edit chapters/NN.json by hand, or re-run.")
    print(f"Next:   python3 -m story_pipeline.cli record {b.root} --dry-run")


def cmd_record(args, cfg):
    b = _bundle(args)
    # The same gate `write` puts in front of an unapproved outline. Narration is
    # the biggest cost here, and it used to run against chapters the editor had
    # failed. --dry-run is always allowed: it is free and is how you decide.
    if not b.all_chapters_approved() and not args.dry_run and not args.force:
        written = [n for n in b.chapter_numbers() if b.chapter_json(n).exists()]
        left = [n for n in written if not b.chapter_approved(n)]
        # A chapter approved and then rewritten shows up here again, which is
        # the point: the approval was for prose that no longer exists.
        lapsed = [n for n in left if str(n) in b.chapter_approvals()]
        unpassed = b.manifest().get("stage_info", {}).get("edit", {}).get("unpassed") or []
        sys.exit(
            f"{len(written) - len(left)} of {len(written)} chapters approved. "
            f"Not yet: {', '.join(str(n) for n in left)}\n"
            + (f"Changed since you approved them: {lapsed}\n" if lapsed else "")
            + (f"Chapters that failed review: {unpassed}\n" if unpassed else "")
            + f"  python3 -m story_pipeline.cli review\n"
              f"  python3 -m story_pipeline.cli approve {b.root} --chapter {left[0]}\n"
              "or pass --force to narrate them anyway."
        )
    est = actor.estimate(cfg, b)
    print(f"{est['words']} words / {est['chars']} characters")
    print(f"~{est['runtime_min']} min narration on {est['engine']}")
    print(f"{est['billed_units']} {est['unit']}"
          + (f", about ${est['usd']:.2f}" if est["usd"] else ""))
    if args.dry_run:
        print("\nDry run. Nothing synthesized.")
        return
    actor.record_all(cfg, b, force=args.force)
    # Only now does story.md carry a measured runtime rather than an assumed one.
    summary.write(b)
    print(f"\nSummary: {b.story_md_path}")
    print(f"Next:   python3 -m story_pipeline.cli design {b.root}")


def cmd_attribute(args, cfg):
    """Put dialogue attribution into a story written for a cast.

    **This spends money** — a writer and an editor call per chapter, the same
    loop `write` uses, seeded with one standing note. Everything written before
    narration went solo needs it: the old `writer.md` forbade `said Mrs Pike`
    because seven voices made it redundant, and one voice makes it essential.

    Chapters with no dialogue are skipped rather than paid for. Every chapter it
    does rewrite lapses its approval and its narration, which is correct — the
    words changed — and means a re-read and a re-record.
    """
    b = _bundle(args)
    numbers = args.chapter or [n for n in b.chapter_numbers()
                               if b.chapter_json(n).exists()]
    todo, skipped = [], []
    for n in numbers:
        if not b.chapter_json(n).exists():
            continue
        (todo if text.chapter_dialogue_turns(b, n) else skipped).append(n)

    if skipped:
        print(f"No dialogue, skipping: {', '.join(str(n) for n in skipped)}")
    if not todo:
        sys.exit("Nothing to attribute.")

    # Tags are worth asking for only if the model will perform them. Default
    # follows the provider; --tags/--no-tags overrides when you know you are
    # about to switch models.
    model = cfg["elevenlabs"]["model"]
    performs = tts.tags_enabled(cfg)
    with_tags = performs if args.tags is None else args.tags
    if with_tags:
        print(f"audio tags: asked for — {model} performs them"
              + ("" if performs else "  (forced with --tags; this model strips them)"))
    else:
        print("audio tags: not asked for — "
              + ("you passed --no-tags" if performs
                 else f"{model} strips them (--tags to ask anyway)"))

    est = [text.estimate_redraft(cfg, b, n) for n in todo]
    low = sum(e.get("low", 0) for e in est)
    high = sum(e.get("high", 0) for e in est)
    print(f"{len(todo)} chapter(s) to rewrite: {', '.join(str(n) for n in todo)}")
    if all(e.get("known") for e in est):
        print(f"about ${low:.2f}, up to ${high:.2f} if the editor sends every one back")
    else:
        print("cost unknown — ledger/rates.json has no price for these models")
    print("Each chapter's approval and its narration lapse: the words change.")
    if args.dry_run:
        print("\nDry run. Nothing rewritten.")
        return
    if not args.yes:
        if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("stopped.")
            return

    done = []
    for n in todo:
        print(f"\nchapter {n}: sending to the writer...")
        try:
            review = text.redraft_chapter(cfg, b, n,
                                          text.attribution_note(with_tags))
        except Exception as e:                    # one bad chapter is not the story
            print(f"  failed: {type(e).__name__}: {e}")
            continue
        done.append(n)
        print(f"  {'passed' if review.get('pass') else 'FAILED'} "
              f"({review.get('score', '?')}), {b.chapter_words(n)} words")
    summary.write(b)

    print(f"\n{len(done)} of {len(todo)} rewritten. Read them before recording:")
    print(f"  python3 -m story_pipeline.cli review")
    print("Then re-approve, and record — the narration is marked out of date, so "
          "`record` will\nredo those chapters rather than keep audio of words "
          "that have changed.")


def cmd_tts_check(args, cfg):
    """Synthesize one line and report what the model actually returns.

    The timeline, the subtitles and the video sync are all built from character
    alignment, and whether a given model returns it is not something the docs
    will tell you. Nor will they tell you whether an audio tag is performed or
    read out loud. Both are a few hundred credits to find out here, against
    25,000 to find out during a story.
    """
    provider = tts.build(cfg)
    if not hasattr(provider, "probe"):
        sys.exit(f"{provider.name} has no probe")
    voices = cfg["casting"]
    voice = args.voice or voices.get("solo_voice") or voices["narrator"]
    line = args.text or (
        "[quietly] Old Mr Fenner is dead, and the beam is cracked through "
        "with the damp. Fifteen pounds, and the poor box holds four."
    )
    tags = getattr(provider, "tags", False)
    print(f"model:  {cfg['elevenlabs']['model']}")
    print(f"tags:   {'sent — this model performs them' if tags else 'STRIPPED before sending'}")
    joins = ("previous/next text sent — prosody carries across a join"
             if getattr(provider, "context", False)
             else "no continuity fields — this model refuses them")
    print(f"joins:  {joins}")
    print(f"voice:  {voice}")
    print(f"text:   {line}\n")
    r = provider.probe(line, voice)

    # A check whose verdict is "listen to it" has to leave something to listen
    # to. auditions/ is already gitignored and already means exactly this.
    out = Path("auditions") / f"tts-check-{cfg['elevenlabs']['model']}-{voice}.mp3"
    actor._encode(r["pcm"], r["sample_rate"], out)

    print(f"alignment returned: yes ({len(r['sentences'])} sentence(s))")
    print(f"billed: {r['billed']} credits, {r['duration_ms'] / 1000:.1f}s of audio")
    for s in r["sentences"]:
        print(f"  [{s['start_ms']:>6} - {s['end_ms']:>6}ms] {s['text']}")
    print(f"\naudio:  {out}")
    print(f"  open {out}")
    if tags:
        print("\nA tag was sent. If you can hear it spoken rather than performed, "
              "set\nelevenlabs.audio_tags: false and it will be stripped from "
              "every line.")
    else:
        print("\nThe tag was stripped before sending, so it cost nothing and was "
              "not read\nout. Switch elevenlabs.model to eleven_v3 and run this "
              "again to hear one\nperformed.")


def cmd_voices(args, cfg):
    """List the account's real voices, so voice_library can be corrected."""
    provider = tts.build(cfg)
    if not hasattr(provider, "list_voices"):
        sys.exit(f"{provider.name} has no voice listing; see its own docs")

    library = {v: k for k, v in cfg["elevenlabs"]["voice_library"].items()}
    for v in provider.list_voices():
        labels = v.get("labels") or {}
        traits = ", ".join(filter(None, [labels.get("accent"), labels.get("gender"),
                                         labels.get("age"), labels.get("use_case")]))
        mapped = library.get(v["voice_id"])
        flag = f"  [voice_library: {mapped}]" if mapped else ""
        print(f"{v['name']:<20} {v['voice_id']}  {traits}{flag}")


def cmd_design(args, cfg):
    b = _bundle(args)
    designer.design_all(cfg, b, replan=getattr(args, "replan", False))
    print(f"Next:   python3 -m story_pipeline.cli direct {b.root}")


def cmd_direct(args, cfg):
    b = _bundle(args)
    if b.video_path.exists() and not args.force:
        sys.exit(f"{b.video_path} exists. Pass --force to overwrite.")
    director.direct(cfg, b)


def cmd_redraft(args, cfg):
    """Send one written chapter back to the writer with a note. **Paid.**

    The same call the review page's *Note to writer* makes. It exists as a
    command because the page now runs every stage as a subprocess — and because
    a note worth sending is worth being able to send from a terminal.
    """
    b = _bundle(args)
    note = args.note
    if args.note_file:
        note = Path(args.note_file).read_text()

    def progress(attempt, review):
        _progress(event="pass", attempt=attempt, ok=bool(review.get("pass")),
                  score=review.get("score"), words=b.chapter_words(args.chapter))

    review = text.redraft_chapter(cfg, b, args.chapter, note, on_pass=progress)
    summary.write(b)
    verdict = "passed" if review.get("pass") else "still failing review"
    print(f"\nChapter {args.chapter}: {verdict}"
          + (f" ({review['score']})" if review.get("score") else ""))
    later = [m for m in b.chapter_numbers()
             if m > args.chapter and b.chapter_json(m).exists()]
    if later:
        print(f"Chapters {later} were written with the old version as context "
              "and are unchanged — worth rereading.")


def cmd_lint(args, cfg):
    """Check drafted chapters for machine register without paying for an editor pass."""
    b = _bundle(args)
    total = 0
    for n in b.chapter_numbers():
        if not b.chapter_json(n).exists():
            continue
        findings = linter.lint_chapter(b.chapter(n))
        total += len(linter.errors(findings))
        if not findings:
            print(f"chapter {n}: clean")
            continue
        print(f"chapter {n}: {len(linter.errors(findings))} error(s), "
              f"{len(findings) - len(linter.errors(findings))} warning(s)")
        for f in findings:
            print(f"  [{f.severity}] {f.rule}: {f.quote}")
    sys.exit(1 if total else 0)


def cmd_render(args, cfg):
    """Rewrite every chapter's markdown from the JSON already on disk.

    `chapters/NN.md` is only a view of `chapters/NN.json`, so improving how it
    renders leaves chapters written earlier looking the old way. This catches
    them up. It reads and writes local files and calls nothing — free, and safe
    to run at any point, including after narration: the spoken text lives in the
    JSON, so the markdown changing cannot make a recording stale.
    """
    b = _bundle(args)
    if args.strip_titles:
        removed = text.strip_title_segments(b)
        if removed:
            print(f"Removed {len(removed)} announced title(s):")
            for n, said in sorted(removed.items()):
                print(f"  chapter {n}: {said!r}")
            print("The narration adds these from casting.chapter_announcement.")
        else:
            print("No chapter titles written into the prose.")
        return
    outline = b.outline()
    done = 0
    for n in b.chapter_numbers():
        if not b.chapter_json(n).exists():
            continue
        b.chapter_md(n).write_text(
            text.render_markdown(b.chapter(n), outline,
                                      attribute=not args.plain))
        done += 1
    style = "plain" if args.plain else "with speaker labels"
    print(f"Re-rendered {done} chapter(s) {style}."
          if done else "No written chapters to render.")


def cmd_critique(args, cfg):
    """Re-run the editor over chapters already written, changing nothing.

    `write` couples judging to drafting: the only way to get a fresh verdict was
    to pay for a new chapter and lose the one you had. That is the wrong trade
    after a hand edit, and it is the wrong trade after the rules change — the
    editor has gained several checks that the chapters on disk were never held
    to, and re-reading them costs one editor call each against a rewrite's
    writer-plus-editor.

    The chapter is not touched. Only `reviews/NN.json` is rewritten, so a verdict
    that now fails will make the chapter eligible for `write` again — which is
    the point, but is worth knowing before running it on a story you thought was
    finished.
    """
    b = _bundle(args)
    numbers = args.chapter or [n for n in b.chapter_numbers()
                               if b.chapter_json(n).exists()]
    if not numbers:
        sys.exit("No written chapters to critique.")

    est = 0.02 * len(numbers)
    print(f"Re-reading {len(numbers)} chapter(s) with {cfg['models']['editor']} "
          f"— roughly ${est:.2f}, and nothing is rewritten.")
    if not args.yes:
        if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("stopped.")
            return

    for n in numbers:
        if not b.chapter_json(n).exists():
            print(f"chapter {n}: not written")
            continue
        was = json.loads(b.review_path(n).read_text()) if b.review_path(n).exists() else {}
        r = text.review_chapter(cfg, b, n)
        moved = ""
        if was and bool(was.get("pass")) != bool(r.get("pass")):
            moved = f"  (was {'pass' if was.get('pass') else 'FAIL'})"
        print(f"\nchapter {n}: {'passed' if r.get('pass') else 'FAILED'} "
              f"({r.get('score', '?')}){moved}")
        for label, key in [("continuity", "continuity"), ("listenability", "listenability"),
                           ("anachronisms", "anachronisms"), ("fixes", "fixes")]:
            for item in (r.get(key) or [])[:4]:
                print(f"    {label}: {item if isinstance(item, str) else json.dumps(item)}"[:150])

    failed = [n for n in numbers if b.review_path(n).exists()
              and not json.loads(b.review_path(n).read_text()).get("pass")]
    if failed:
        print(f"\n{len(failed)} chapter(s) now fail: {failed}")
        print("Fix them by hand in `cli review`, or let the writer do it:")
        print(f"  python3 -m story_pipeline.cli write {b.root}")
        print("which rewrites exactly those, since a failing chapter is no longer done.")
    else:
        print("\nAll chapters still pass.")


def cmd_status(args, cfg):
    b = _bundle(args)
    m = b.manifest()
    print(f"{m['slug']}\n")
    for stage in STAGES:
        print(f"  {stage:8} {m['stages'].get(stage, 'pending')}")
    # Per-chapter state lives in reviews/NN.json, which is neither the prose nor
    # the chapter JSON — so it was effectively invisible unless you went looking
    # for a directory nothing mentions. This is the place to read it.
    numbers = b.chapter_numbers()
    if numbers and any(b.chapter_json(n).exists() for n in numbers):
        spec = text.length_spec(m.get("pinned", {}).get("length_min"))
        print(f"\n{'ch':>3}  {'ok':>3}  {'words':>6}  {'target':>6}  {'drafts':>6}  verdict")
        total = 0
        for n in numbers:
            if not b.chapter_json(n).exists():
                print(f"{n:>3}  {'':>3}  {'—':>6}  {'':>6}  {'':>6}  not written")
                continue
            words = b.chapter_words(n)
            total += words
            target = next((c.get("target_words", 0) for c in b.outline()["chapters"]
                           if c["n"] == n), 0)
            r = {}
            if b.review_path(n).exists():
                try:
                    r = json.loads(b.review_path(n).read_text())
                except json.JSONDecodeError:
                    pass
            if not r:
                verdict = "written, not reviewed"
            elif r.get("exhausted"):
                verdict = f"kept after {cfg['max_edit_passes']} revisions — still failing"
            elif r.get("pass"):
                verdict = f"passed ({r.get('score', '?')})"
            else:
                verdict = f"FAILED ({len(r.get('fixes') or [])} fixes outstanding)"
            over = f"  {words / target * 100 - 100:+.0f}%" if target else ""
            stale = "  narration stale" if b.narration_stale(n) else ""
            mark = ("ok" if b.chapter_approved(n)
                    else ("!" if str(n) in b.chapter_approvals() else ""))
            print(f"{n:>3}  {mark:>3}  {words:>6,}  {target:>6,}  "
                  f"{len(b.drafts(n)):>6}  {verdict}{over}{stale}")

        if total:
            mins = total / text.WPM
            print(f"\n{total:,} words ~{mins:.0f} min against a {spec['minutes']}-minute "
                  f"target ({mins / spec['minutes'] * 100 - 100:+.0f}%)")
            written = [n for n in numbers if b.chapter_json(n).exists()]
            ok = b.approved_chapters()
            lapsed = [n for n in written
                      if str(n) in b.chapter_approvals() and n not in ok]
            print(f"Approved: {len(ok)} of {len(written)}"
                  + (f"  ·  ! = approved then rewritten: {lapsed}" if lapsed else ""))
            print(f"Reviews: {b.root}/reviews/NN.json")

    measured = calibration.measure(b)
    if measured:
        print("\n" + calibration.report(measured))

    if m.get("spend"):
        print("\nSpend:")
        for k, v in m["spend"].items():
            print(f"  {k:16} {v}")
    # `ideate` pins this as "genre_voices"; "voices" is the per-story map the
    # actor pins once it has cast the characters. Show whichever exists.
    voices = m.get("pinned", {}).get("voices") or m.get("pinned", {}).get("genre_voices")
    if voices:
        print("\nCasting:")
        for k, v in voices.items():
            print(f"  {k:20} {v}")
    if b.story_md_path.exists():
        print(f"\nSummary: {b.story_md_path}")


def cmd_all(args, cfg):
    """No review gate. For batches you intend to triage after the fact."""
    b = text.ideate(cfg, cfg["stories_dir"], args.genre, args.subgenre,
                    _read_notes(args), args.bible, length_min=args.length)
    text.approve(b)
    print(f"{b.outline()['title']}\n")
    text.run_text_stages(cfg, b)
    est = actor.estimate(cfg, b)
    print(f"\n{est['words']} words, ~{est['runtime_min']} min, ${est['usd']:.2f} narration")
    actor.record_all(cfg, b, force=args.force)
    summary.write(b)
    designer.design_all(cfg, b)
    director.direct(cfg, b)
    summary.write(b)
    print(f"\nDone: {b.video_path}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="story_pipeline", description=__doc__)
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_idea_args(sp):
        # Not required when --bible is given: a bible declares its own genre.
        sp.add_argument("--genre", choices=genres.available())
        sp.add_argument("--bible", help="series bible; see: cli bibles")
        sp.add_argument("--subgenre", help="see: cli genres")
        sp.add_argument("--notes", help="notes to shape the story")
        sp.add_argument("--notes-file", help="the same, read from a file")
        sp.add_argument(
            "--length", type=int, choices=sorted(text.LENGTHS),
            help="target runtime in minutes (default: length_min in config.yaml). "
                 "Pinned at creation; revise will not change it.",
        )

    new = sub.add_parser("new", help="ideate an outline, then stop for review")
    add_idea_args(new)
    new.set_defaults(fn=cmd_new)

    hist = sub.add_parser("history",
                          help="what the channel has repeated; free, no API calls")
    hist.add_argument("--bible", help="scope to one series")
    hist.add_argument("--genre", choices=genres.available(),
                      help="show the full trope menu with the weights applied")
    hist.set_defaults(fn=cmd_history)

    rv = sub.add_parser("review", help="review queue in a browser; free, no API calls")
    rv.add_argument("--port", type=int, default=8765)
    rv.add_argument("--no-open", action="store_true", help="don't open a browser")
    rv.add_argument("--reload", action="store_true",
                    help="restart when story_pipeline/ changes; waits for any "
                         "in-flight request so a paid call is never interrupted")
    rv.set_defaults(fn=cmd_review)

    vo = sub.add_parser("voices", help="list your TTS account's voices and IDs")
    vo.set_defaults(fn=cmd_voices)

    bib = sub.add_parser("bibles", help="list series bibles and their recurring cast")
    bib.set_defaults(fn=cmd_bibles)

    bnew = sub.add_parser("bible", help="scaffold a new series bible")
    bnew_sub = bnew.add_subparsers(dest="bible_cmd", required=True)
    bn = bnew_sub.add_parser("new")
    bn.add_argument("name")
    bn.add_argument("--genre", required=True, choices=genres.available())
    bn.add_argument("--series", help="display name, e.g. 'Middle Watch: Amberlight'")
    bn.add_argument("--example", action="store_true",
                    help="scaffold filled in rather than commented out, so the "
                         "YAML shape is unambiguous and `bible check` passes")
    bn.set_defaults(fn=cmd_bible_new)

    bn.add_argument("--author", help="who to credit in the changelog")

    bc = bnew_sub.add_parser("check", help="validate a bible; free, no API calls")
    bc.add_argument("name", nargs="?", help="omit to check every bible")
    bc.set_defaults(fn=cmd_bible_check)

    bl = bnew_sub.add_parser("log", help="record or show a bible's change history")
    bl.add_argument("name")
    bl.add_argument("-m", "--message", help="what changed; omit to show the history")
    bl.add_argument("--author", help="who to credit "
                                     "(default: config author, then git, then login)")
    bl.add_argument("--kind", default="edit",
                    help="entry type, e.g. edit / canon / cast (default: edit)")
    bl.add_argument("--limit", type=int, default=20,
                    help="how many entries to show (default: 20)")
    bl.set_defaults(fn=cmd_bible_log)

    gen = sub.add_parser("genres", help="list genres, subgenres and tropes")
    gen.add_argument("--tropes", action="store_true", help="include the full trope menu")
    gen.set_defaults(fn=cmd_genres)

    rev = sub.add_parser("revise", help="regenerate the outline against review notes")
    rev.add_argument("story")
    rev.add_argument("feedback", help="what to change, in plain English")
    rev.set_defaults(fn=cmd_revise)

    app = sub.add_parser("approve", help="approve the outline and unlock the writer")
    app.add_argument("story")
    app.add_argument("--chapters", action="store_true",
                     help="approve every drafted chapter, unlocking record")
    app.add_argument("--chapter", type=int, action="append", metavar="N",
                     help="approve one chapter; repeatable, and how to work "
                          "through a story a chapter at a time")
    app.set_defaults(fn=cmd_approve)

    wr = sub.add_parser("write", help="draft and edit every chapter")
    wr.add_argument("story")
    wr.add_argument("--force", action="store_true", help="skip the outline review gate")
    wr.add_argument("--restart", action="store_true",
                    help="rewrite chapters already done instead of resuming")
    wr.set_defaults(fn=cmd_write)

    rdf = sub.add_parser("redraft",
                         help="send one chapter back to the writer with a note")
    rdf.add_argument("story")
    rdf.add_argument("--chapter", type=int, required=True)
    rdf.add_argument("--note", default="", help="what the writer should change")
    rdf.add_argument("--note-file", help="the same, read from a file")
    rdf.set_defaults(fn=cmd_redraft)

    dz = sub.add_parser("design", help="cast sheet and scene images")
    dz.add_argument("story")
    dz.add_argument("--replan", action="store_true",
                    help="choose the shots again instead of keeping the plan on "
                         "disk; a paid call per chapter")
    dz.set_defaults(fn=cmd_design)

    for name, fn, helptext in [
        ("status", cmd_status, "show stage state and spend"),
        ("lint", cmd_lint, "check drafts for machine register (free, no API calls)"),
    ]:
        s = sub.add_parser(name, help=helptext)
        s.add_argument("story", help="path to the story bundle")
        s.set_defaults(fn=fn)

    rn = sub.add_parser("render",
                        help="rebuild chapter markdown from the JSON (free, no API calls)")
    rn.add_argument("story")
    rn.add_argument("--plain", action="store_true",
                    help="reading copy: paragraphs, but no speaker labels")
    rn.add_argument("--strip-titles", action="store_true",
                    help="remove chapter titles written into the prose; the "
                         "narration adds them from a template now")
    rn.set_defaults(fn=cmd_render)

    at = sub.add_parser("attribute",
                        help="add dialogue attribution for solo narration; paid")
    at.add_argument("story")
    at.add_argument("--chapter", type=int, action="append", metavar="N",
                    help="just this chapter; repeatable")
    at.add_argument("--tags", action=argparse.BooleanOptionalAction, default=None,
                    help="ask for audio tags as well; defaults to whether the "
                         "configured model performs them")
    at.add_argument("--dry-run", action="store_true", help="cost only, rewrite nothing")
    at.add_argument("--yes", "-y", action="store_true", help="skip the spend prompt")
    at.set_defaults(fn=cmd_attribute)

    tc = sub.add_parser("tts-check",
                        help="synthesize one line to verify alignment and tags; "
                             "costs a few hundred credits")
    tc.add_argument("--text", help="the line to speak; defaults to a tagged sample")
    tc.add_argument("--voice", help="voice name; defaults to the narrator")
    tc.set_defaults(fn=cmd_tts_check)

    cq = sub.add_parser("critique",
                        help="re-run the editor on written chapters without rewriting them")
    cq.add_argument("story")
    cq.add_argument("--chapter", type=int, action="append", metavar="N",
                    help="just this chapter; repeatable")
    cq.add_argument("--yes", "-y", action="store_true", help="skip the spend prompt")
    cq.set_defaults(fn=cmd_critique)

    rec = sub.add_parser("record", help="synthesize narration")
    rec.add_argument("story")
    rec.add_argument("--dry-run", action="store_true", help="cost and format check only")
    rec.add_argument("--force", action="store_true", help="skip the chapter review gate")
    rec.set_defaults(fn=cmd_record)

    dr = sub.add_parser("direct", help="assemble the video")
    dr.add_argument("story")
    dr.add_argument("--force", action="store_true")
    dr.set_defaults(fn=cmd_direct)

    allp = sub.add_parser("all", help="every stage end to end, no review gate")
    add_idea_args(allp)
    allp.set_defaults(fn=cmd_all)

    args = p.parse_args(argv)
    # Before anything else, so every command sees the credentials. Previously
    # only calibration-sweep.sh sourced .env, which meant the same command
    # worked from the script and failed from the shell.
    cfgmod.load_env()
    cfg = cfgmod.load(args.config)
    args.fn(args, cfg)


if __name__ == "__main__":
    main()
