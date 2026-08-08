"""Command line: argument parsing, output files and exit codes.

No analysis happens here. Everything this module knows how to do is call one of
the library entry points and decide what to print and what to return to the
shell.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
from pathlib import Path

from binfer import __version__
from binfer.analyze import Options, analyze
from binfer.corpus import CorpusError, load_corpus
from binfer.model import Confidence
from binfer.records import MIN_RECORD_SIZE
from binfer.report import render_json, render_ksy, render_scorecard, render_text
from binfer.synth import score_all

EXIT_OK = 0
EXIT_ERROR = 1

DESCRIPTION = "Infer the structure of an unknown binary format from a corpus of samples."

GETTING_STARTED = """
binfer compares a directory of sample files and reports the structure they share.
It needs a directory; there is nothing useful it can do with none.

  binfer --self-test              check the tool against formats it knows the answer to
  binfer C:\\path\\to\\samples       analyse your own samples
  binfer --help                   every option

Put at least four files of the same unknown format in one directory. Twelve or
more makes the statistics worth trusting; below that the report says so.
"""


def _identifier(name: str) -> str:
    """Turn a directory name into something Kaitai will accept as an id."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return cleaned if cleaned and not cleaned[0].isdigit() else f"format_{cleaned}"


def _positive(minimum: int) -> object:
    def parse(text: str) -> int:
        value = int(text)
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be {minimum} or more")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, which is the whole of the v1.0 surface."""
    parser = argparse.ArgumentParser(prog="binfer", description=DESCRIPTION)
    parser.add_argument("directory", nargs="?", type=Path, help="directory of sample files")
    parser.add_argument("--version", action="version", version=f"binfer {__version__}")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="analyse synthetic corpora with known ground truth and grade the result",
    )
    parser.add_argument("--json", type=Path, metavar="FILE", help="also write findings as JSON")
    parser.add_argument("--ksy", type=Path, metavar="FILE", help="also write a Kaitai Struct draft")
    parser.add_argument(
        "--min-confidence",
        choices=[tier.label for tier in Confidence],
        default=Confidence.LOW.label,
        help="hide findings below this tier (default: low)",
    )
    parser.add_argument(
        "--max-files", type=_positive(1), metavar="N", help="analyse at most N samples"
    )
    parser.add_argument(
        "--record-size",
        type=_positive(MIN_RECORD_SIZE),
        metavar="N",
        help="segment records of N bytes instead of inferring the size from a count field",
    )
    parser.add_argument("--no-color", action="store_true", help="never emit ANSI colour")
    return parser


def launched_from_explorer() -> bool:
    """Return whether this process owns its console alone.

    A console started by Explorer for a double-clicked program holds only that
    program, so the window vanishes the moment it exits. Started from a shell,
    the console holds the shell too. Anything other than a count of one, or any
    failure to ask, means do not pause.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes  # noqa: PLC0415

        buffer = (ctypes.c_uint * 2)()
        attached = ctypes.windll.kernel32.GetConsoleProcessList(buffer, 2)
    except (AttributeError, OSError):
        return False
    return attached == 1


def _wait_for_reader() -> None:
    print("\nPress Enter to close this window.", end="")
    with contextlib.suppress(EOFError, KeyboardInterrupt):
        input()


def _wants_colour(stream: object, *, disabled: bool) -> bool:
    if disabled or os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _run_self_test(*, colour: bool) -> int:
    cards = score_all()
    print(render_scorecard(cards, colour=colour), end="")
    return EXIT_OK if all(card.passed for card in cards) else EXIT_ERROR


def _run_analysis(args: argparse.Namespace, *, colour: bool) -> int:
    corpus = load_corpus(args.directory, max_files=args.max_files)
    report = analyze(
        corpus,
        Options(
            record_size=args.record_size,
            min_confidence=Confidence.from_label(args.min_confidence),
        ),
    )
    print(render_text(report, colour=colour), end="")
    if args.json:
        _write(args.json, render_json(report))
    if args.ksy:
        _write(args.ksy, render_ksy(report, _identifier(args.directory.name)))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the requested job and return the exit code."""
    parser = build_parser()
    requested = sys.argv[1:] if argv is None else argv
    if not requested:
        # Asking for nothing is not a usage error. Someone has double-clicked a
        # downloaded executable, and an argparse complaint in a window that
        # closes itself tells them nothing.
        parser.print_help()
        print(GETTING_STARTED, end="")
        if launched_from_explorer():
            _wait_for_reader()
        return EXIT_OK

    args = parser.parse_args(requested)
    colour = _wants_colour(sys.stdout, disabled=args.no_color)

    if args.self_test:
        code = _run_self_test(colour=colour)
    elif args.directory is None:
        parser.error("a sample directory is required unless --self-test is given")
    else:
        try:
            code = _run_analysis(args, colour=colour)
        except (CorpusError, OSError) as error:
            print(f"binfer: {error}", file=sys.stderr)
            code = EXIT_ERROR

    if launched_from_explorer():
        _wait_for_reader()
    return code
