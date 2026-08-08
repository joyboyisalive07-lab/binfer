"""Tests for the command line: arguments, output files and exit codes."""

from __future__ import annotations

import io
import json
import sys
from typing import TYPE_CHECKING

import pytest

from binfer import __version__, cli
from binfer.cli import EXIT_ERROR, EXIT_OK, _identifier, main
from binfer.synth import FORMATS, SAMPLES_PER_FORMAT, format_by_key, generate

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

USAGE_EXIT = 2


def corpus_dir(root: Path, key: str = "C", count: int = 24) -> Path:
    target = root / f"corpus_{key.lower()}"
    target.mkdir(parents=True, exist_ok=True)
    for index, data in enumerate(generate(format_by_key(key), count)):
        (target / f"sample_{index:03d}.bin").write_bytes(data)
    return target


def test_version_prints_and_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == EXIT_OK
    assert capsys.readouterr().out.strip() == f"binfer {__version__}"


def test_self_test_grades_every_format_and_succeeds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--self-test"]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "SELF TEST" in printed
    assert f"{len(FORMATS)} of {len(FORMATS)} formats fully recovered" in printed
    for fmt in FORMATS:
        assert f"  {fmt.key.ljust(5)}{fmt.name}" in printed


def test_the_scorecard_fits_a_hundred_columns(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--self-test"])
    for line in capsys.readouterr().out.splitlines():
        assert len(line) <= 100, line


def test_a_directory_is_analysed_and_the_report_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(corpus_dir(tmp_path)), "--no-color"]) == EXIT_OK
    printed = capsys.readouterr().out
    for section in ("CORPUS", "LAYOUT", "RELATIONS", "REGIONS", "NOTES"):
        assert f"\n{section}\n" in f"\n{printed}"
    assert "records at 0x0010" in printed


def test_json_and_ksy_are_written_only_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = corpus_dir(tmp_path)
    assert main([str(source), "--no-color"]) == EXIT_OK
    assert not list(tmp_path.glob("*.json"))

    findings = tmp_path / "findings.json"
    draft = tmp_path / "draft.ksy"
    assert (
        main([str(source), "--no-color", "--json", str(findings), "--ksy", str(draft)]) == EXIT_OK
    )
    capsys.readouterr()
    assert json.loads(findings.read_text(encoding="utf-8"))["tool"] == "binfer"
    assert "id: corpus_c" in draft.read_text(encoding="utf-8")


def test_a_missing_directory_reports_the_reason_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path / "absent")]) == EXIT_ERROR
    assert "not a directory" in capsys.readouterr().err


def test_a_corpus_that_is_too_small_reports_the_reason_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(corpus_dir(tmp_path, "A", count=3))]) == EXIT_ERROR
    assert "at least 4" in capsys.readouterr().err


def scripted(*answers: str) -> Callable[[], str]:
    """Replay the given answers in place of input(), then report end of input."""
    queue = list(answers)
    return lambda: queue.pop(0) if queue else (_ for _ in ()).throw(EOFError)


def test_no_arguments_with_input_redirected_shows_help(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "someone_is_watching", lambda: False)
    assert main([]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "usage: binfer" in printed
    assert "binfer --self-test" in printed
    assert "error:" not in printed


def test_no_arguments_with_a_keyboard_offers_a_choice(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "someone_is_watching", lambda: True)
    monkeypatch.setattr("builtins.input", scripted("q"))
    assert main([]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "Choose 1, 2, 3 or q" in printed
    assert "drag a folder onto binfer.exe" in printed
    assert "Press Enter to close this window." in printed


def test_an_interactive_stdin_is_enough_without_the_console_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console-owner count is a fallback, not the thing the menu depends on.

    Terminal hosts differ in what they attach to a console, so a tool that only
    asked that question could stay silent on the machine that needed it most.
    """
    monkeypatch.setattr(cli, "launched_from_explorer", lambda: False)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    assert cli.someone_is_watching() is True


def test_a_redirected_stdin_under_explorer_still_counts_as_watched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "launched_from_explorer", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    assert cli.someone_is_watching() is True


def test_nobody_is_watching_a_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "launched_from_explorer", lambda: False)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    assert cli.someone_is_watching() is False


def test_a_closed_stdin_does_not_crash_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "launched_from_explorer", lambda: False)
    monkeypatch.setattr(sys, "stdin", None)
    assert cli.someone_is_watching() is False


def test_the_menu_can_run_the_self_test(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.interactive_session(scripted("1"), colour=False) == EXIT_OK
    assert "7 of 7 formats fully recovered" in capsys.readouterr().out


def test_the_menu_can_analyse_a_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = corpus_dir(tmp_path)
    assert cli.interactive_session(scripted("2", str(source)), colour=False) == EXIT_OK
    printed = capsys.readouterr().out
    assert "CORPUS" in printed
    assert "records at 0x0010" in printed


def test_the_menu_accepts_a_path_the_way_explorer_quotes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = corpus_dir(tmp_path)
    assert cli.interactive_session(scripted("2", f'"{source}"'), colour=False) == EXIT_OK
    assert "CORPUS" in capsys.readouterr().out


def test_the_menu_can_write_examples_and_analyse_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Someone who just downloaded the executable has nothing to point option 2 at."""
    target = tmp_path / "examples"
    assert cli.interactive_session(scripted("3", str(target)), colour=False) == EXIT_OK
    written = sorted(target.glob("sample_*.bin"))
    assert len(written) == SAMPLES_PER_FORMAT
    printed = capsys.readouterr().out
    assert f"Wrote {SAMPLES_PER_FORMAT} files to" in printed
    assert "CORPUS" in printed
    assert "records at 0x0010" in printed


def test_writing_examples_can_be_declined(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.interactive_session(scripted("3", "q"), colour=False) == EXIT_OK
    assert not list(tmp_path.iterdir())
    assert "The example format is" in capsys.readouterr().out


def test_writing_examples_reports_a_folder_it_cannot_create(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "taken"
    blocker.write_text("not a folder", encoding="utf-8")
    assert cli.interactive_session(scripted("3", str(blocker)), colour=False) == EXIT_OK
    assert "binfer:" in capsys.readouterr().err


def test_the_menu_asks_again_after_a_path_that_is_not_a_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = corpus_dir(tmp_path)
    reader = scripted("9", "2", str(tmp_path / "nowhere"), str(source))
    assert cli.interactive_session(reader, colour=False) == EXIT_OK
    printed = capsys.readouterr().out
    assert "Type 1, 2, 3 or q." in printed
    assert "is not a folder" in printed
    assert "CORPUS" in printed


def test_the_menu_survives_a_closed_input_stream(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.interactive_session(scripted(), colour=False) == EXIT_OK
    assert "Choose 1, 2, 3 or q" in capsys.readouterr().out


def test_the_menu_reports_a_corpus_it_cannot_use(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = corpus_dir(tmp_path, "A", count=3)
    assert cli.interactive_session(scripted("2", str(source)), colour=False) == EXIT_ERROR
    assert "at least 4" in capsys.readouterr().err


def test_a_directory_is_still_required_when_other_options_are_given() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--no-color"])
    assert exit_info.value.code == USAGE_EXIT


def test_the_window_is_held_open_only_when_explorer_started_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(cli, "launched_from_explorer", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: prompts.append("waited") or "")

    assert main([str(corpus_dir(tmp_path, "A")), "--no-color"]) == EXIT_OK
    assert prompts == ["waited"]
    assert "Press Enter to close this window." in capsys.readouterr().out

    monkeypatch.setattr(cli, "launched_from_explorer", lambda: False)
    prompts.clear()
    assert main([str(corpus_dir(tmp_path, "A")), "--no-color"]) == EXIT_OK
    assert prompts == []


def test_explorer_detection_is_false_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert cli.launched_from_explorer() is False


def test_max_files_limits_the_corpus(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(corpus_dir(tmp_path)), "--no-color", "--max-files", "8"]) == EXIT_OK
    assert "8 files analysed of 24 found" in capsys.readouterr().out


def test_max_files_rejects_a_meaningless_count() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["x", "--max-files", "0"])
    assert exit_info.value.code == USAGE_EXIT


def test_record_size_rejects_a_meaningless_size() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["x", "--record-size", "1"])
    assert exit_info.value.code == USAGE_EXIT


def test_record_size_overrides_the_inferred_segmentation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(corpus_dir(tmp_path)), "--no-color", "--record-size", "16"]) == EXIT_OK
    assert "(--record-size 16)" in capsys.readouterr().out


def test_min_confidence_hides_the_weaker_tiers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = corpus_dir(tmp_path)
    assert main([str(source), "--no-color", "--min-confidence", "proved"]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "high " not in printed
    assert "below the requested confidence" in printed


def test_min_confidence_rejects_a_tier_that_does_not_exist() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["x", "--min-confidence", "medium"])
    assert exit_info.value.code == USAGE_EXIT


def test_colour_is_suppressed_by_the_flag_and_by_the_environment(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = corpus_dir(tmp_path, "A")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    main([str(source), "--no-color"])
    assert "\x1b[" not in capsys.readouterr().out

    monkeypatch.setenv("NO_COLOR", "1")
    main([str(source)])
    assert "\x1b[" not in capsys.readouterr().out


def test_directory_names_become_usable_kaitai_identifiers() -> None:
    assert _identifier("My Saves 2024") == "my_saves_2024"
    assert _identifier("2024-dumps") == "format_2024_dumps"
    assert _identifier("...") == "format_"


def test_an_unwritable_output_path_fails_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = corpus_dir(tmp_path, "A")
    assert main([str(source), "--no-color", "--json", str(tmp_path / "missing" / "x.json")]) == (
        EXIT_ERROR
    )
    assert "binfer:" in capsys.readouterr().err
