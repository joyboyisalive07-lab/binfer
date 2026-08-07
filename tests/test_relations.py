"""Tests for length, count, pointer and checksum relations."""

from __future__ import annotations

import random
import struct

import pytest

from binfer import relations
from binfer.corpus import Corpus, Sample, plan_alignment
from binfer.model import Confidence, RelationKind
from binfer.relations import (
    checksum_pool,
    crc16_forward,
    crc16_reflected,
    find_checksum_relations,
    find_relations,
    fit_linear,
    span_candidates,
)
from binfer.synth import FORMATS, build_corpus, format_by_key

CHECK_INPUT = b"123456789"


def corpus_of(blobs: list[bytes]) -> Corpus:
    samples = tuple(Sample(f"s{index:03d}.bin", data) for index, data in enumerate(blobs))
    return Corpus(samples=samples, discovered=len(samples))


def analyse(blobs: list[bytes]) -> relations.RelationResult:
    corpus = corpus_of(blobs)
    return find_relations(corpus, plan_alignment(corpus))


def test_crc16_variants_match_their_published_check_values() -> None:
    assert crc16_reflected(CHECK_INPUT, 0x0000) == 0xBB3D
    assert crc16_reflected(CHECK_INPUT, 0xFFFF) == 0x4B37
    assert crc16_forward(CHECK_INPUT, 0xFFFF) == 0x29B1
    assert crc16_forward(CHECK_INPUT, 0x0000) == 0x31C3


def test_crc16_of_nothing_is_the_initial_value() -> None:
    assert crc16_reflected(b"", 0x1234) == 0x1234
    assert crc16_forward(b"", 0x1234) == 0x1234


def test_fit_linear_finds_an_exact_affine_relation() -> None:
    values = [10, 20, 30, 40]
    assert fit_linear(values, [30, 40, 50, 60]) == (1, 20)
    assert fit_linear(values, [160, 320, 480, 640]) == (16, 0)


def test_fit_linear_rejects_anything_approximate() -> None:
    assert fit_linear([10, 20, 30], [30, 40, 51]) is None
    assert fit_linear([10, 20, 30], [30, 45, 60]) is None
    assert fit_linear([7, 7, 7], [10, 10, 10]) is None
    assert fit_linear([10, 20], [60, 40]) is None


def test_fit_linear_rejects_an_implausible_stride_or_negative_constant() -> None:
    assert fit_linear([1, 2], [1, 100_000]) is None
    assert fit_linear([10, 20], [0, 10]) is None


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_declared_relations_are_all_proved(fmt) -> None:  # noqa: ANN001
    corpus = build_corpus(fmt)
    found = find_relations(corpus, plan_alignment(corpus)).relations
    for truth in fmt.relations:
        match = [
            relation
            for relation in found
            if relation.kind is truth.kind and relation.subject_offset == truth.subject_offset
        ]
        assert match, f"{fmt.key}: no {truth.kind.value} at {truth.subject_offset} ({truth.role})"
        assert match[0].evidence.unanimous


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_no_relation_is_invented_beyond_the_declared_ones(fmt) -> None:  # noqa: ANN001
    corpus = build_corpus(fmt)
    found = find_relations(corpus, plan_alignment(corpus)).relations
    declared = {(truth.kind, truth.subject_offset) for truth in fmt.relations}
    assert {(relation.kind, relation.subject_offset) for relation in found} == declared


def test_length_and_checksum_relations_are_proved_and_pointers_are_high() -> None:
    found = find_relations(
        build_corpus(format_by_key("B")), plan_alignment(build_corpus(format_by_key("B")))
    )
    assert all(relation.confidence is Confidence.PROVED for relation in found.relations)

    pointer = find_relations(
        build_corpus(format_by_key("D")), plan_alignment(build_corpus(format_by_key("D")))
    )
    assert pointer.relations[0].confidence is Confidence.HIGH


def test_a_count_relation_reports_the_record_stride() -> None:
    corpus = build_corpus(format_by_key("C"))
    result = find_relations(corpus, plan_alignment(corpus))
    assert [(hint.stride, hint.subject) for hint in result.record_hints] == [(16, "0x0004 u32le")]
    count = next(r for r in result.relations if r.kind is RelationKind.COUNT)
    assert "16-byte records" in count.summary


def test_random_files_of_equal_size_yield_no_relations() -> None:
    rng = random.Random(999)
    assert analyse([rng.randbytes(96) for _ in range(16)]).relations == ()


def test_random_files_of_varying_size_yield_no_relations() -> None:
    rng = random.Random(1000)
    blobs = [rng.randbytes(rng.randrange(80, 160)) for _ in range(16)]
    assert analyse(blobs).relations == ()


def test_a_trailing_crc16_is_found_and_named() -> None:
    rng = random.Random(31)
    blobs = []
    for _ in range(12):
        body = b"CR16" + rng.randbytes(6)
        blobs.append(body + struct.pack("<H", crc16_forward(body, 0xFFFF)))
    found = analyse(blobs).relations
    assert [(r.kind, r.subject, r.summary) for r in found] == [
        (RelationKind.CHECKSUM, "0x000A u16le", "crc16-ccitt of everything before the field")
    ]


def test_a_header_sum_over_the_body_is_found() -> None:
    rng = random.Random(32)
    blobs = []
    for _ in range(12):
        body = rng.randbytes(40)
        blobs.append(b"SUM!" + bytes([sum(body) & 0xFF]) + b"\x00\x00\x00" + body)
    found = analyse(blobs).relations
    assert [(r.subject, r.summary) for r in found] == [
        ("0x0004 u8", "sum of from the field to EOF")
    ]


def test_the_same_field_is_not_reported_twice_under_overlapping_readings() -> None:
    corpus = build_corpus(format_by_key("C"))
    found = find_relations(corpus, plan_alignment(corpus)).relations
    offsets = [relation.subject_offset for relation in found]
    assert len(offsets) == len(set(offsets))


def test_relations_are_reproducible() -> None:
    corpus = build_corpus(format_by_key("B"))
    alignment = plan_alignment(corpus)
    assert find_relations(corpus, alignment) == find_relations(corpus, alignment)


def test_a_uniform_corpus_is_not_scanned_from_both_ends() -> None:
    corpus = build_corpus(format_by_key("A"))
    candidates = span_candidates(corpus, plan_alignment(corpus))
    assert candidates
    assert all(candidate.anchor >= 0 for candidate in candidates)


def test_crc16_is_skipped_and_reported_when_samples_are_large(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(relations, "CRC16_MAX_SAMPLE_BYTES", 1)
    rng = random.Random(33)
    blobs = []
    for _ in range(8):
        body = b"CR16" + rng.randbytes(6)
        blobs.append(body + struct.pack("<H", crc16_forward(body, 0xFFFF)))
    corpus = corpus_of(blobs)
    found, notes = find_checksum_relations(corpus, span_candidates(corpus, plan_alignment(corpus)))
    assert found == ()
    assert any("CRC-16" in note for note in notes)


def test_the_checksum_search_reports_when_its_budget_truncates_it(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(relations, "CHECKSUM_BUDGET_BYTES", 1)
    monkeypatch.setattr(relations, "MIN_CHECKSUM_SPANS", 2)
    rng = random.Random(34)
    corpus = corpus_of([rng.randbytes(64) for _ in range(8)])
    _, notes = find_checksum_relations(corpus, span_candidates(corpus, plan_alignment(corpus)))
    assert any("candidate spans" in note for note in notes)


def test_trailers_are_searched_before_headers() -> None:
    corpus = build_corpus(format_by_key("B"))
    pool = checksum_pool(span_candidates(corpus, plan_alignment(corpus)))
    anchors = [candidate.anchor for candidate in pool]
    assert anchors[0] < 0
    assert anchors[-1] >= 0
