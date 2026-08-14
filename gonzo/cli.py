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
)
from gonzo.modes.chat import ChatSession
from gonzo.modes.compose import Composer
from gonzo.modes.critique import Critic
from gonzo.modes.transfer import Transferrer
from gonzo.scoring.metrics import score_text
from gonzo.style.spec import load_spec


def _read_input(source: str | None) -> str:
    """Read from a file path, or from stdin when given '-' or nothing."""
    if source and source != "-":
        try:
            return Path(source).read_text(encoding="utf-8")
        except OSError as exc:
            sys.exit(f"error: {exc}")
    if sys.stdin.isatty():
        sys.exit("error: no input. Pass a file path, or pipe text on stdin.")
    return sys.stdin.read()


def cmd_chat(args: argparse.Namespace) -> int:
    session = ChatSession(client=GonzoClient(), seed=args.seed)
    print("Gonzo engine. Ctrl-D or /quit to exit, /reset to clear, /seed to show.\n")

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

        print()
        try:
            for chunk in session.stream(line):
                if isinstance(chunk, StreamReset):
                    # A fallback superseded what we just printed. A terminal
                    # cannot un-print, so mark the boundary plainly.
                    print("\n[the previous model declined; restarting]\n", flush=True)
                else:
                    print(chunk, end="", flush=True)
        except (RefusalError, EmptyCompletionError) as exc:
            print(f"\n[{exc}]", file=sys.stderr)
        print("\n")


def cmd_write(args: argparse.Namespace) -> int:
    composer = Composer(client=GonzoClient(), use_judge=not args.no_judge)
    try:
        draft = composer.compose(args.assignment, seed=args.seed, revise=not args.no_revise)
    except RefusalError as exc:
        print(f"declined: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "text": draft.text,
            "revision": draft.revision,
            "directive": draft.directive.to_dict(),
            "report": draft.report.to_dict(),
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

    p = sub.add_parser("chat", help="conversational mode")
    p.add_argument("--seed", type=int, help="reproducible variance")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("write", help="compose a long-form piece")
    p.add_argument("assignment", help="what to cover")
    p.add_argument("--seed", type=int, help="reproducible variance")
    p.add_argument("--no-judge", action="store_true", help="skip the LLM rubric pass")
    p.add_argument("--no-revise", action="store_true", help="return the first draft")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("-q", "--quiet", action="store_true", help="prose only")
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
