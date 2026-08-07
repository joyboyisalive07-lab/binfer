"""Tests for field typing, scored against the declared ground truth."""

from __future__ import annotations

import itertools
import random
import struct

import pytest

from binfer.corpus import plan_alignment
from binfer.model import Confidence, Endian, Evidence
from binfer.stats import column_stats
from binfer.synth import FORMAT_C_HEADER_SIZE, FORMATS, build_corpus, format_by_key
from binfer.types import (
    PRINTABLE,
    Hypothesis,
    _is_bimodal_sign,
    _slope_score,
    _string_hypothesis,
    _Window,
    infer_fields,
)


def head_fields(key: str) -> tuple[dict[int, object], tuple]:
    fmt = format_by_key(key)
    corpus = build_corpus(fmt)
    alignment = plan_alignment(corpus)
    window = corpus.head_window(alignment.head_size)
    fields = infer_fields(window, column_stats(window))
    return {field.offset: field for field in fields}, fields


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_declared_fields_are_recovered_with_an_accepted_type(fmt) -> None:  # noqa: ANN001
    found, _ = head_fields(fmt.key)
    for truth in fmt.fields:
        field = found.get(truth.offset)
        assert field is not None, f"{fmt.key}: nothing found at {truth.offset:#x} ({truth.role})"
        assert field.size == truth.size, f"{fmt.key}: wrong size at {truth.offset:#x}"
        assert field.type_name in truth.accepted, (
            f"{fmt.key}: {truth.offset:#x} read as {field.type_name}, expected one of "
            f"{sorted(truth.accepted)}"
        )


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_every_finding_carries_a_unanimous_or_low_evidence_count(fmt) -> None:  # noqa: ANN001
    _, fields = head_fields(fmt.key)
    assert fields
    for field in fields:
        assert field.evidence.total == 24
        assert field.evidence.claim
        if field.confidence is Confidence.HIGH:
            assert field.evidence.unanimous


def test_record_layout_is_typed_when_it_falls_inside_the_head_window() -> None:
    fmt = format_by_key("C")
    found, _ = head_fields("C")
    for truth in fmt.record_fields:
        offset = FORMAT_C_HEADER_SIZE + truth.offset
        field = found.get(offset)
        assert field is not None, f"nothing found at {offset:#x} ({truth.role})"
        assert field.type_name in truth.accepted


def test_typed_fields_never_overlap_and_are_sorted() -> None:
    for fmt in FORMATS:
        _, fields = head_fields(fmt.key)
        offsets = [field.offset for field in fields]
        assert offsets == sorted(offsets)
        for earlier, later in itertools.pairwise(fields):
            assert earlier.end <= later.offset


def test_typing_is_reproducible() -> None:
    first, _ = head_fields("G")
    second, _ = head_fields("G")
    assert [(k, v) for k, v in sorted(first.items())] == [(k, v) for k, v in sorted(second.items())]


def test_zero_padding_is_left_uncovered_for_the_region_stage() -> None:
    found, _ = head_fields("A")
    assert all(offset < 0x14 for offset in found)


def test_random_corpus_yields_no_typed_fields() -> None:
    rng = random.Random(4242)
    window = [rng.randbytes(64) for _ in range(24)]
    assert infer_fields(window, column_stats(window)) == ()


def test_constant_corpus_is_reported_as_magic_and_const() -> None:
    window = [b"HEAD" + bytes([0x01, 0x00])] * 8
    fields = infer_fields(window, column_stats(window))
    assert [(f.offset, f.type_name, f.value_repr) for f in fields] == [
        (0, "magic[4]", "'HEAD'"),
        (4, "const[2]", "01 00"),
    ]


def test_padding_is_left_uncovered_except_for_one_high_byte() -> None:
    window = [bytes([index]) + b"\x00" * 6 for index in range(8)]
    fields = infer_fields(window, column_stats(window))
    assert [(field.offset, field.size) for field in fields] == [(0, 2)]


def string_hypothesis(rows: list[bytes]) -> object:
    return _string_hypothesis(_Window(rows=rows, columns=column_stats(rows)), 0, utf16=False)


def test_a_string_holding_in_every_sample_is_reported_at_the_high_tier() -> None:
    rows = [f"name{index:02d}".encode() + bytes(2) for index in range(22)]
    found = string_hypothesis(rows)
    assert found.type_name == "ascii[8]"
    assert found.confidence is Confidence.HIGH
    assert (found.evidence.hits, found.evidence.total) == (22, 22)


def test_a_string_holding_in_most_samples_is_reported_at_the_low_tier() -> None:
    rows = [f"name{index:02d}".encode() + bytes(2) for index in range(20)]
    rows += [b"\xff\xfe\xfd\xfc\xfb\xfa\xf9\xf8", b"\x01\x02\x03\x04\x05\x06\x07\x08"]
    found = string_hypothesis(rows)
    assert found.confidence is Confidence.LOW
    assert (found.evidence.hits, found.evidence.total) == (20, 22)


def test_a_string_holding_in_too_few_samples_is_not_reported_at_all() -> None:
    rows = [f"name{index:02d}".encode() + bytes(2) for index in range(4)]
    rows += [bytes(range(index, index + 8)) for index in range(0x80, 0xA0, 2)]
    assert string_hypothesis(rows) is None


def test_a_close_rival_is_reported_as_the_runner_up() -> None:
    found, _ = head_fields("A")
    assert found[0x06].type_name == "enum16le"
    assert found[0x06].runner_up == "enum8"
    assert found[0x08].runner_up is None


def test_big_endian_data_is_not_read_as_little_endian() -> None:
    rng = random.Random(11)
    window = [struct.pack(">I", rng.randrange(0, 2_000_000_000)) for _ in range(24)]
    fields = infer_fields(window, column_stats(window))
    assert [field.type_name for field in fields] == ["u32be"]


def test_signed_data_is_preferred_over_the_unsigned_reading() -> None:
    rng = random.Random(12)
    window = [struct.pack("<i", rng.randrange(-2_000_000, 2_000_001)) for _ in range(24)]
    fields = infer_fields(window, column_stats(window))
    assert [field.type_name for field in fields] == ["i32le"]


def test_slope_score_punishes_entropy_that_grows_towards_the_high_byte() -> None:
    assert _slope_score([4.0, 1.0], 4.0) == 1.0
    assert _slope_score([1.0, 4.0], 4.0) == pytest.approx(0.25)
    assert _slope_score([3.0], 4.0) == 1.0
    assert _slope_score([3.0, 1.0], 0.0) == 1.0


def test_sign_extension_is_recognised_only_at_both_ends_of_the_byte() -> None:
    assert _is_bimodal_sign([0x00, 0x01, 0xFE, 0xFF])
    assert not _is_bimodal_sign([0x00, 0x01, 0x02])
    assert not _is_bimodal_sign([0xFE, 0xFF])
    assert not _is_bimodal_sign([0x00, 0x40, 0xFF])


def test_printable_set_excludes_control_bytes_and_del() -> None:
    assert 0x20 in PRINTABLE
    assert 0x7E in PRINTABLE
    assert 0x7F not in PRINTABLE
    assert 0x1F not in PRINTABLE


def test_hypothesis_converts_to_a_field_with_its_runner_up() -> None:
    hypothesis = Hypothesis(
        offset=8,
        size=4,
        type_name="u32le",
        score=0.9,
        confidence=Confidence.HIGH,
        value_repr="1..2",
        evidence=Evidence("top 1 byte(s) constant zero", 24, 24),
    )
    assert hypothesis.end == 12
    field = hypothesis.as_field("u16le")
    assert field.runner_up == "u16le"
    assert field.offset == 8


def test_endian_prefixes_drive_the_struct_readers() -> None:
    assert Endian.LITTLE.struct_prefix + "I" == "<I"
    assert Endian.BIG.struct_prefix + "I" == ">I"
