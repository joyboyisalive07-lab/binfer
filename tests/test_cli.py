"""Tests for the command line: arguments, output files and exit codes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from binfer import __version__
from binfer.cli import EXIT_ERROR, EXIT_OK, _identifier, main
from binfer.synth import FORMATS, format_by_key, generate

if TYPE_CHECKING:
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


def test_a_directory_is_required_unless_self_testing() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == USAGE_EXIT


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
