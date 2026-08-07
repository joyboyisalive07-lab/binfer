"""Tests for corpus loading, size classification and window alignment."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from binfer.corpus import (
    MIN_SAMPLES,
    WINDOW_LIMIT,
    AlignmentMode,
    CorpusError,
    load_corpus,
    plan_alignment,
)

if TYPE_CHECKING:
    from pathlib import Path


def write_samples(directory: Path, blobs: dict[str, bytes]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in blobs.items():
        (directory / name).write_bytes(data)
    return directory


def uniform_corpus(directory: Path, count: int = 16, size: int = 32) -> Path:
    blobs = {f"s{index:03d}.bin": bytes([index]) * size for index in range(count)}
    return write_samples(directory, blobs)


def test_missing_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="not a directory"):
        load_corpus(tmp_path / "absent")


def test_too_few_samples_is_rejected(tmp_path: Path) -> None:
    write_samples(tmp_path, {f"s{i}.bin": b"abcd" for i in range(MIN_SAMPLES - 1)})
    with pytest.raises(CorpusError, match="at least 4"):
        load_corpus(tmp_path)


def test_small_corpus_loads_but_warns(tmp_path: Path) -> None:
    uniform_corpus(tmp_path, count=5)
    corpus = load_corpus(tmp_path)
    assert corpus.count == 5
    assert any("only 5 samples" in warning for warning in corpus.warnings)


def test_large_enough_corpus_produces_no_warning(tmp_path: Path) -> None:
    uniform_corpus(tmp_path, count=16)
    assert load_corpus(tmp_path).warnings == ()


def test_empty_files_are_skipped_and_reported(tmp_path: Path) -> None:
    uniform_corpus(tmp_path, count=12)
    (tmp_path / "zzz-empty.bin").write_bytes(b"")
    corpus = load_corpus(tmp_path)
    assert corpus.count == 12
    assert corpus.discovered == 13
    assert corpus.warnings == ("skipped 1 empty file(s)",)


def test_subdirectories_are_not_traversed(tmp_path: Path) -> None:
    uniform_corpus(tmp_path, count=12)
    write_samples(tmp_path / "nested", {"deep.bin": b"\x00" * 32})
    assert load_corpus(tmp_path).count == 12


def test_max_files_takes_the_first_names(tmp_path: Path) -> None:
    uniform_corpus(tmp_path, count=20)
    corpus = load_corpus(tmp_path, max_files=6)
    assert [sample.name for sample in corpus.samples] == [f"s{i:03d}.bin" for i in range(6)]
    assert corpus.discovered == 20


def test_summary_reports_uniform_sizes(tmp_path: Path) -> None:
    uniform_corpus(tmp_path, count=12, size=64)
    summary = load_corpus(tmp_path).summary()
    assert summary.uniform
    assert (summary.min_size, summary.max_size, summary.median_size) == (64, 64, 64)
    assert summary.mean_size == 64.0
    assert summary.distinct_sizes == 1


def test_summary_reports_varying_sizes(tmp_path: Path) -> None:
    write_samples(tmp_path, {f"s{i}.bin": b"\x00" * (10 + i) for i in range(4)})
    summary = load_corpus(tmp_path).summary()
    assert not summary.uniform
    assert (summary.min_size, summary.max_size) == (10, 13)
    assert summary.distinct_sizes == 4


def test_windows_are_aligned_slices(tmp_path: Path) -> None:
    write_samples(
        tmp_path, {f"s{i}.bin": bytes([i]) + b"MID" + bytes([0xF0 + i]) for i in range(4)}
    )
    corpus = load_corpus(tmp_path)
    assert corpus.head_window(2) == (b"\x00M", b"\x01M", b"\x02M", b"\x03M")
    assert corpus.tail_window(1) == (b"\xf0", b"\xf1", b"\xf2", b"\xf3")
    assert corpus.tail_window(0) == (b"", b"", b"", b"")


def test_uniform_corpus_aligns_the_whole_file(tmp_path: Path) -> None:
    uniform_corpus(tmp_path, count=8, size=48)
    alignment = plan_alignment(load_corpus(tmp_path))
    assert alignment.mode is AlignmentMode.FIXED
    assert (alignment.head_size, alignment.tail_size) == (48, 0)
    assert alignment.middle(48) == (48, 48)


def test_varying_corpus_splits_into_head_and_tail(tmp_path: Path) -> None:
    write_samples(tmp_path, {f"s{i}.bin": b"\x00" * (100 + 10 * i) for i in range(4)})
    alignment = plan_alignment(load_corpus(tmp_path))
    assert alignment.mode is AlignmentMode.HEAD_TAIL
    assert (alignment.head_size, alignment.tail_size) == (50, 50)
    assert alignment.middle(130) == (50, 80)
    assert alignment.tail_offset(130) == 80


def test_windows_never_overlap_on_the_smallest_sample(tmp_path: Path) -> None:
    write_samples(tmp_path, {f"s{i}.bin": b"\x00" * (7 + i) for i in range(4)})
    alignment = plan_alignment(load_corpus(tmp_path))
    assert alignment.head_size + alignment.tail_size <= 7
    assert alignment.middle(7) == (3, 4)


def test_windows_are_capped_for_huge_uniform_samples(tmp_path: Path) -> None:
    size = 3 * WINDOW_LIMIT
    write_samples(tmp_path, {f"s{i}.bin": bytes([i]) * size for i in range(4)})
    alignment = plan_alignment(load_corpus(tmp_path))
    assert alignment.mode is AlignmentMode.FIXED
    assert (alignment.head_size, alignment.tail_size) == (WINDOW_LIMIT, WINDOW_LIMIT)
    assert alignment.middle(size) == (WINDOW_LIMIT, size - WINDOW_LIMIT)
