"""Tests for the shared data model and overlap resolution."""

from __future__ import annotations

import pytest

from binfer.model import Confidence, Endian, Evidence, Field, resolve_overlaps


def make_field(
    offset: int,
    size: int,
    confidence: Confidence,
    type_name: str = "u8",
) -> Field:
    return Field(
        offset=offset,
        size=size,
        type_name=type_name,
        value_repr="0",
        confidence=confidence,
        evidence=Evidence("identical", 4, 4),
    )


def test_confidence_is_ordered_from_low_to_proved() -> None:
    assert Confidence.LOW < Confidence.HIGH < Confidence.PROVED


def test_confidence_round_trips_through_its_label() -> None:
    for tier in Confidence:
        assert Confidence.from_label(tier.label) is tier


def test_confidence_rejects_an_unknown_label() -> None:
    with pytest.raises(ValueError, match="unknown confidence tier"):
        Confidence.from_label("certain")


def test_endian_maps_to_struct_prefixes() -> None:
    assert Endian.LITTLE.struct_prefix == "<"
    assert Endian.BIG.struct_prefix == ">"


def test_evidence_renders_the_supporting_count() -> None:
    assert Evidence("identical", 40, 40).render() == "identical in 40/40"
    assert Evidence("decodes to 2019-2026", 38, 40).unanimous is False


def test_field_overlap_is_half_open() -> None:
    first = make_field(0, 4, Confidence.HIGH)
    adjacent = make_field(4, 4, Confidence.HIGH)
    straddling = make_field(2, 4, Confidence.HIGH)
    assert not first.overlaps(adjacent)
    assert first.overlaps(straddling)
    assert straddling.overlaps(first)


def test_resolve_overlaps_prefers_the_stronger_tier() -> None:
    weak = make_field(0, 4, Confidence.LOW, "u32le")
    strong = make_field(2, 4, Confidence.PROVED, "u32be")
    assert resolve_overlaps([weak, strong]) == (strong,)


def test_resolve_overlaps_prefers_wider_coverage_within_a_tier() -> None:
    narrow = make_field(0, 2, Confidence.HIGH, "u16le")
    wide = make_field(0, 4, Confidence.HIGH, "u32le")
    assert resolve_overlaps([narrow, wide]) == (wide,)


def test_resolve_overlaps_keeps_disjoint_fields_sorted_by_offset() -> None:
    tail = make_field(8, 4, Confidence.LOW)
    head = make_field(0, 4, Confidence.HIGH)
    assert resolve_overlaps([tail, head]) == (head, tail)


def test_resolve_overlaps_is_independent_of_input_order() -> None:
    fields = [
        make_field(0, 4, Confidence.HIGH, "u32le"),
        make_field(0, 2, Confidence.HIGH, "u16le"),
        make_field(3, 4, Confidence.LOW, "u32be"),
        make_field(8, 8, Confidence.PROVED, "crc32"),
    ]
    expected = resolve_overlaps(fields)
    assert resolve_overlaps(reversed(fields)) == expected
    assert resolve_overlaps(sorted(fields, key=lambda f: f.type_name)) == expected
