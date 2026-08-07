"""Tests for record segmentation and the recursion into a record body."""

from __future__ import annotations

import random
import struct

from binfer.corpus import Corpus, Sample, plan_alignment
from binfer.records import (
    MIN_POOLED_RECORDS,
    find_records,
    pool_records,
    segment,
)
from binfer.relations import find_relations
from binfer.synth import (
    FORMAT_C_HEADER_SIZE,
    FORMAT_C_RECORD_SIZE,
    FORMATS,
    build_corpus,
    format_by_key,
)


def corpus_of(blobs: list[bytes]) -> Corpus:
    samples = tuple(Sample(f"s{index:03d}.bin", data) for index, data in enumerate(blobs))
    return Corpus(samples=samples, discovered=len(samples))


def records_of(key: str, **kwargs: int) -> tuple:
    corpus = build_corpus(format_by_key(key))
    hints = find_relations(corpus, plan_alignment(corpus)).record_hints
    return find_records(corpus, hints, **kwargs)


def test_the_record_array_is_found_where_the_count_field_says() -> None:
    layouts = records_of("C")
    assert len(layouts) == 1
    layout = layouts[0]
    assert layout.start == FORMAT_C_HEADER_SIZE
    assert layout.record_size == FORMAT_C_RECORD_SIZE
    assert layout.count_repr == "4..12"
    assert layout.origin == "count field at 0x0004 u32le"
    assert layout.evidence.unanimous


def test_the_record_body_matches_its_declared_layout() -> None:
    fmt = format_by_key("C")
    found = {field.offset: field for field in records_of("C")[0].fields}
    for truth in fmt.record_fields:
        field = found.get(truth.offset)
        assert field is not None, f"nothing found at +{truth.offset:#x} ({truth.role})"
        assert field.size == truth.size
        assert field.type_name in truth.accepted


def test_pooling_records_exposes_a_bitfield_that_one_window_could_not_prove() -> None:
    flags = next(field for field in records_of("C")[0].fields if field.offset == 0x05)
    assert flags.type_name == "bits8"
    assert "8 combinations" in flags.evidence.claim


def test_no_records_are_reported_for_formats_without_a_count_field() -> None:
    for fmt in FORMATS:
        if fmt.record_size:
            continue
        assert records_of(fmt.key) == ()


def test_an_explicit_record_size_reproduces_the_inferred_layout() -> None:
    corpus = build_corpus(format_by_key("C"))
    manual = find_records(corpus, (), record_size=FORMAT_C_RECORD_SIZE)
    assert len(manual) == 1
    assert manual[0].origin == "--record-size 16"
    assert manual[0].start == FORMAT_C_HEADER_SIZE
    assert manual[0].fields == records_of("C")[0].fields


def test_the_start_search_prefers_the_alignment_with_the_lowest_column_entropy() -> None:
    corpus = build_corpus(format_by_key("C"))
    chosen = segment(corpus, FORMAT_C_RECORD_SIZE)
    assert chosen is not None
    assert chosen.start == FORMAT_C_HEADER_SIZE
    assert chosen.total_records == sum(chosen.counts)
    assert chosen.entropy < 1.0


def test_pool_records_slices_every_sample_into_equal_rows() -> None:
    corpus = corpus_of([bytes(range(12)) for _ in range(4)])
    pooled = pool_records(corpus, 4, 4)
    assert pooled == (bytes(range(4, 8)), bytes(range(8, 12))) * 4
    assert all(len(row) == 4 for row in pooled)


def test_segmentation_refuses_a_stride_that_cannot_fit() -> None:
    corpus = corpus_of([bytes(8) for _ in range(4)])
    assert segment(corpus, 1) is None
    assert segment(corpus, 8) is None
    assert segment(corpus, 64) is None


def test_a_segmentation_explaining_no_field_is_not_reported() -> None:
    rng = random.Random(555)
    corpus = corpus_of([rng.randbytes(64) for _ in range(12)])
    assert find_records(corpus, (), record_size=16) == ()


def test_too_few_pooled_records_are_not_described() -> None:
    body = b"".join(struct.pack("<I4s", index, b"REC!") for index in range(2))
    corpus = corpus_of([b"HEAD" + body for _ in range(3)])
    assert sum(len(sample.data) - 4 for sample in corpus.samples) // 8 < MIN_POOLED_RECORDS
    assert find_records(corpus, (), record_size=8) == ()


def test_a_checksum_inside_a_record_is_found_by_the_recursion() -> None:
    rng = random.Random(556)
    blobs = []
    for _ in range(6):
        records = []
        for _ in range(8):
            payload = rng.randbytes(6)
            records.append(payload + bytes([sum(payload) & 0xFF]) + b"\x00")
        blobs.append(b"HDR!" + b"".join(records))
    layouts = find_records(corpus_of(blobs), (), record_size=8)
    assert len(layouts) == 1
    assert [(r.subject, r.summary) for r in layouts[0].relations] == [
        ("0x0006 u8", "sum of everything before the field")
    ]


def test_record_findings_are_reproducible() -> None:
    assert records_of("C") == records_of("C")
