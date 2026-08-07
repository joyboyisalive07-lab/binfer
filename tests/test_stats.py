"""Tests for column statistics and entropy."""

from __future__ import annotations

import math
import random

import pytest

from binfer.stats import (
    MIN_BLOB_BYTES,
    ColumnClass,
    byte_entropy,
    column_stats,
    is_high_entropy_span,
    mean_byte_entropy,
    shannon_entropy,
)


def test_entropy_of_a_single_outcome_is_zero() -> None:
    assert shannon_entropy([7]) == 0.0
    assert shannon_entropy([]) == 0.0
    assert shannon_entropy([0, 0]) == 0.0


def test_entropy_of_a_fair_coin_is_one_bit() -> None:
    assert shannon_entropy([5, 5]) == pytest.approx(1.0)
    assert shannon_entropy([1, 1, 1, 1]) == pytest.approx(2.0)


def test_entropy_does_not_depend_on_count_order() -> None:
    counts = [3, 11, 1, 7, 5, 2]
    reference = shannon_entropy(counts)
    shuffled = counts[:]
    random.Random(1234).shuffle(shuffled)
    assert shannon_entropy(shuffled) == reference
    assert shannon_entropy(reversed(counts)) == reference


def test_byte_entropy_spans_the_full_range() -> None:
    assert byte_entropy(b"") == 0.0
    assert byte_entropy(b"\x00" * 64) == 0.0
    assert byte_entropy(bytes(range(256))) == pytest.approx(8.0)


def test_mean_byte_entropy_averages_over_samples() -> None:
    chunks = [b"\x00" * 16, bytes(range(256))]
    assert mean_byte_entropy(chunks) == pytest.approx(4.0)
    assert mean_byte_entropy([]) == 0.0


def test_random_span_is_recognised_as_a_blob() -> None:
    rng = random.Random(7)
    chunks = [bytes(rng.randrange(256) for _ in range(1024)) for _ in range(4)]
    assert is_high_entropy_span(chunks)


def test_structured_span_is_not_a_blob() -> None:
    chunks = [(b"RECORD\x00\x01" * 128) for _ in range(4)]
    assert not is_high_entropy_span(chunks)


def test_short_spans_are_never_called_blobs() -> None:
    rng = random.Random(9)
    short = bytes(rng.randrange(256) for _ in range(MIN_BLOB_BYTES - 1))
    assert not is_high_entropy_span([short] * 4)
    assert not is_high_entropy_span([])


def test_column_stats_describe_each_offset() -> None:
    window = [b"AB", b"AC", b"AB", b"AD"]
    columns = column_stats(window)
    assert len(columns) == 2

    first, second = columns
    assert first.offset == 0
    assert first.constant
    assert first.value == ord("A")
    assert first.kind is ColumnClass.CONSTANT
    assert first.entropy == 0.0

    assert second.distinct == 3
    assert second.value is None
    assert second.values == (ord("B"), ord("C"), ord("D"))
    assert second.histogram == ((ord("B"), 2), (ord("C"), 1), (ord("D"), 1))
    assert second.entropy == pytest.approx(1.5)


def test_column_stats_respect_a_base_offset() -> None:
    assert [c.offset for c in column_stats([b"xy", b"xz"], base_offset=16)] == [16, 17]


def test_empty_window_yields_no_columns() -> None:
    assert column_stats([]) == ()


def test_ragged_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="argument 2 is shorter"):
        column_stats([b"abc", b"ab"])


def test_all_distinct_column_is_high_entropy_regardless_of_corpus_size() -> None:
    for count in (4, 12, 40):
        window = [bytes([index]) for index in range(count)]
        column = column_stats(window)[0]
        assert column.entropy == pytest.approx(math.log2(count))
        assert column.normalized_entropy == pytest.approx(1.0)
        assert column.kind is ColumnClass.HIGH_ENTROPY


def test_small_closed_value_set_is_low_variance() -> None:
    window = [bytes([index % 3]) for index in range(24)]
    column = column_stats(window)[0]
    assert column.kind is ColumnClass.LOW_VARIANCE


def test_varying_mask_isolates_the_bits_that_move() -> None:
    window = [bytes([0b1010_0000]), bytes([0b1010_0001]), bytes([0b1010_0100])]
    assert column_stats(window)[0].varying_mask == 0b0000_0101
    assert column_stats([b"\x5a"] * 4)[0].varying_mask == 0
