"""Command line: gonzo chat | write | transfer | score."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gonzo.client import (
    CredentialsError,
    EmptyCompletionError,
    GonzoClient,
    RefusalError,
    StreamReset,
    WireSearch,
    WireSources,
)
from gonzo.config import WIRE_DEFAULT
from gonzo.modes.chat import ChatSession
from gonzo.modes.compose import Composer
from gonzo.modes.critique import Critic
from gonzo.modes.transfer import Transferrer
from gonzo.scoring.metrics import score_text
from gonzo.style.spec import load_spec


def _read_input(source: str | None) -> str:
    """Read from a file path, or from stdin when given '-' or nothing.

    Usage and I/O failures exit 2, not 1 — exit 1 is the documented "the
    prose failed the bar" verdict, and a missing file must not read as a
    style judgment in scripts that branch on it.
    """
    if source and source != "-":
        try:
            return Path(source).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
    if sys.stdin.isatty():
        print("error: no input. Pass a file path, or pipe text on stdin.", file=sys.stderr)
        raise SystemExit(2)
    return sys.stdin.read()


def _scrub(text: str) -> str:
    """Strip control characters from wire-sourced text before it reaches the
    terminal. Titles and URLs come verbatim from third-party web pages; a
    page whose <title> embeds ESC/CSI/OSC sequences must not be able to
    rewrite the citation line it appears on, retitle the terminal, or poke
    the emulator. (The web UI already treats this data as hostile — the CLI
    gets the same discipline.)"""
    return "".join(ch for ch in text if ch.isprintable())


def _print_sources(sources, out=sys.stderr) -> None:
    if not sources:
        return
    print("\n  off the wire:", file=out)
    for i, s in enumerate(sources, 1):
        title = f" — {_scrub(s.title)}" if s.title else ""
        print(f"    [{i}] {_scrub(s.url)}{title}", file=out)


def cmd_chat(args: argparse.Namespace) -> int:
    wire = WIRE_DEFAULT if args.wire is None else args.wire
    session = ChatSession(client=GonzoClient(), seed=args.seed, wire=wire)
    status = "wire on — searches are billed" if wire else "wire off"
    print(
        f"Gonzo engine ({status}). Ctrl-D or /quit to exit, /reset to clear, "
        "/seed to show, /wire to toggle search.\n"
    )

    while True:
        try:
            line = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in {"/quit", "/exit"}:
            return 0
        if line == "/reset":
            session.reset()
            print("(history cleared)\n")
            continue
        if line == "/seed":
            d = session.last_directive
            print(f"(seed {d.seed}, {d.opening_move}/{d.dominant_band}/{d.imagery_domain})\n" if d else "(no turn yet)\n")
            continue
        if line == "/wire":
            session.wire = not session.wire
            print(f"(wire {'on — searches are billed' if session.wire else 'off'})\n")
            continue

        print()
        try:
            for chunk in session.stream(line):
                if isinstance(chunk, StreamReset):
                    # A fallback superseded what we just printed. A terminal
                    # cannot un-print, so mark the boundary plainly.
                    print("\n[the previous model declined; restarting]\n", flush=True)
                elif isinstance(chunk, WireSearch):
                    # The query is model-generated, but it can embed verbatim
                    # user or page text — scrub it like everything else that
                    # crosses from the wire to the TTY.
                    label = f": {_scrub(chunk.query)}" if chunk.query else ""
                    print(f"[checking the wire{label}]", file=sys.stderr, flush=True)
                elif isinstance(chunk, WireSources):
                    pass  # printed after the reply, from session.last_sources
                else:
                    print(chunk, end="", flush=True)
        except (RefusalError, EmptyCompletionError) as exc:
            print(f"\n[{exc}]", file=sys.stderr)
        _print_sources(session.last_sources)
        print("\n")


def cmd_write(args: argparse.Namespace) -> int:
    wire = WIRE_DEFAULT if args.wire is None else args.wire
    composer = Composer(client=GonzoClient(), use_judge=not args.no_judge, wire=wire)
    try:
        draft = composer.compose(args.assignment, seed=args.seed, revise=not args.no_revise)
    except RefusalError as exc:
        print(f"declined: {exc}", file=sys.stderr)
        return 2
    except EmptyCompletionError as exc:
        print(f"no prose returned: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "text": draft.text,
            "revision": draft.revision,
            "directive": draft.directive.to_dict(),
            "report": draft.report.to_dict(),
            "sources": [s.to_dict() for s in draft.sources],
        }, indent=2))
        # Same contract in both output formats: a draft that misses the bar
        # exits non-zero whether or not --json was passed.
        return 0 if draft.report.passed else 1

    print(draft.text)
    if not args.quiet:
        d = draft.directive
        print("\n" + "─" * 68, file=sys.stderr)
        print(
            f"seed {d.seed} · {d.opening_move} · {d.dominant_band}→{d.contrast_band} · "
            f"{d.imagery_domain} · {d.template} · revision {draft.revision}",
            file=sys.stderr,
        )
        print(draft.report.summary(), file=sys.stderr)
        _print_sources(draft.sources)
    return 0 if draft.report.passed else 1


def cmd_transfer(args: argparse.Namespace) -> int:
    source = _read_input(args.file)
    try:
        result = Transferrer(client=GonzoClient()).transfer(source, seed=args.seed)
    except RefusalError as exc:
        print(f"declined: {exc}", file=sys.stderr)
        return 2

    print(result.text)
    if not args.quiet:
        print("\n" + "─" * 68, file=sys.stderr)
        print(result.report.summary(), file=sys.stderr)
        if result.dropped_facts:
            print(
                "\n  numbers in the source not found in the output "
                "(may be spelled out — check):", file=sys.stderr,
            )
            print(f"    {', '.join(result.dropped_facts)}", file=sys.stderr)
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    text = _read_input(args.file)

    if args.no_judge:
        report = score_text(text, load_spec())
        print(json.dumps(report.to_dict(), indent=2) if args.json else report.summary())
        return 0 if report.passed else 1

    try:
        combined = Critic(client=GonzoClient()).critique(text, assignment=args.assignment)
    except RefusalError as exc:
        print(f"declined: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(combined.to_dict(), indent=2) if args.json else combined.summary())
    return 0 if combined.passed else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("gonzo.server:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gonzo",
        description="A prose engine in the gonzo tradition. Does not impersonate anyone.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def wire_flags(p: argparse.ArgumentParser) -> None:
        # Tri-state: unset falls back to the GONZO_WIRE env default, so a flag
        # can force either direction regardless of the environment.
        p.add_argument(
            "--wire", action="store_true", dest="wire", default=None,
            help="enable web search (billed per search); the narrator cites what it pulls",
        )
        p.add_argument(
            "--no-wire", action="store_false", dest="wire",
            help="disable web search even if GONZO_WIRE is set",
        )

    p = sub.add_parser("chat", help="conversational mode")
    p.add_argument("--seed", type=int, help="reproducible variance")
    wire_flags(p)
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("write", help="compose a long-form piece")
    p.add_argument("assignment", help="what to cover")
    p.add_argument("--seed", type=int, help="reproducible variance")
    p.add_argument("--no-judge", action="store_true", help="skip the LLM rubric pass")
    p.add_argument("--no-revise", action="store_true", help="return the first draft")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("-q", "--quiet", action="store_true", help="prose only")
    wire_flags(p)
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("transfer", help="restyle text, preserving its facts")
    p.add_argument("file", nargs="?", help="input file, or - for stdin")
    p.add_argument("--seed", type=int)
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_transfer)

    p = sub.add_parser("score", help="score a passage against the style spec")
    p.add_argument("file", nargs="?", help="input file, or - for stdin")
    p.add_argument("--assignment", help="what the piece was meant to do")
    p.add_argument("--no-judge", action="store_true", help="metrics only, no API call")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("serve", help="run the web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CredentialsError as exc:
        # The most common setup failure. A traceback here helps nobody.
        print(f"\n{exc}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
