"""Tests for the text, JSON and Kaitai renderers."""

from __future__ import annotations

import json
import random

import pytest

from binfer.analyze import analyze
from binfer.corpus import Corpus, Sample
from binfer.model import Confidence
from binfer.report import (
    OFFSET_COLUMN,
    SIZE_COLUMN,
    TYPE_COLUMN,
    VALUE_COLUMN,
    WIDTH,
    _cell,
    position,
    render_json,
    render_ksy,
    render_text,
)
from binfer.synth import FORMATS, build_corpus, format_by_key

SECTIONS = ("CORPUS", "LAYOUT", "RELATIONS", "REGIONS", "NOTES")


def text_for(key: str, **kwargs: bool) -> str:
    return render_text(analyze(build_corpus(format_by_key(key))), **kwargs)


def test_position_uses_the_start_or_eof_convention() -> None:
    assert position(0) == "0x0000"
    assert position(0x1234) == "0x1234"
    assert position(-4) == "EOF-4"


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_the_five_sections_appear_once_each_in_order(fmt) -> None:  # noqa: ANN001
    lines = render_text(analyze(build_corpus(fmt))).splitlines()
    headings = [line for line in lines if line in SECTIONS]
    assert headings == list(SECTIONS)


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_no_line_is_wider_than_a_hundred_columns(fmt) -> None:  # noqa: ANN001
    for line in render_text(analyze(build_corpus(fmt))).splitlines():
        assert len(line) <= WIDTH, line


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_every_field_row_carries_its_evidence_count(fmt) -> None:  # noqa: ANN001
    report = analyze(build_corpus(fmt))
    rendered = render_text(report)
    for field in report.fields:
        assert field.evidence.render() in rendered


def test_the_same_corpus_renders_byte_identical_text_twice() -> None:
    for fmt in FORMATS:
        first = render_text(analyze(build_corpus(fmt)))
        second = render_text(analyze(build_corpus(fmt)))
        assert first.encode() == second.encode()


def test_a_random_corpus_claims_nothing_beyond_its_size_statistics() -> None:
    rng = random.Random(21)
    corpus = Corpus(
        samples=tuple(Sample(f"r{index}.bin", rng.randbytes(96)) for index in range(16)),
        discovered=16,
    )
    rendered = render_text(analyze(corpus))
    assert "nothing in this corpus could be typed" in rendered
    assert "none proved" in rendered
    assert "unexplained" in rendered
    assert "every file is 96 bytes" in rendered


def test_colour_is_off_by_default_and_wraps_only_the_confidence_column() -> None:
    plain = text_for("A")
    assert "\x1b[" not in plain
    coloured = text_for("A", colour=True)
    assert "\x1b[36mhigh" in coloured
    assert len(coloured.splitlines()) == len(plain.splitlines())


def test_the_record_table_is_nested_under_its_own_heading() -> None:
    rendered = text_for("C")
    assert "records at 0x0010, 16 bytes each, 4..12 per file" in rendered
    assert "  +OFFSET" in rendered
    assert "  +0x0000" in rendered


def test_a_close_runner_up_is_shown_beside_the_winning_type() -> None:
    assert "enum16le / enum8" in text_for("A")


def test_a_cell_always_leaves_a_separator_after_its_text() -> None:
    for text in ("", "u32le", "u32le / enum16le", "u32le / enum16le / more"):
        for width in (6, 12, 17):
            rendered = _cell(text, width)
            assert len(rendered) == width
            assert rendered.endswith(" "), (text, width, rendered)


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_no_field_row_lets_its_columns_run_together(fmt) -> None:  # noqa: ANN001
    """A row that exactly fills a column used to print `enum16le72..75`.

    Checking the line width cannot catch that: a run-together line is shorter
    than a correct one, so it passed the width test with room to spare.
    """
    start = 2 + OFFSET_COLUMN + SIZE_COLUMN + 2  # indent, offset, size, gutter
    rendered = render_text(analyze(build_corpus(fmt))).splitlines()
    layout = rendered[rendered.index("LAYOUT") : rendered.index("RELATIONS")]
    # A field row is the only line carrying a right-aligned size, which keeps
    # the record heading and its evidence line out of the sample.
    size_column = slice(2 + OFFSET_COLUMN, 2 + OFFSET_COLUMN + SIZE_COLUMN)
    rows = [line for line in layout if line[size_column].strip().isdigit()]
    assert rows
    for row in rows:
        type_cell = row[start : start + TYPE_COLUMN]
        assert type_cell.endswith(" "), row
        value_cell = row[start + TYPE_COLUMN : start + TYPE_COLUMN + VALUE_COLUMN]
        assert value_cell.endswith(" "), row


def test_the_widest_reading_still_fits_beside_its_value() -> None:
    assert "u32le / enum16le 72..75" in text_for("D")


def test_an_unexplained_region_says_so_plainly() -> None:
    rendered = text_for("E")
    assert "0x0010..EOF" in rendered
    assert "high-entropy" in rendered
    assert "compressed or encrypted, nothing to recover" in rendered


def test_json_parses_and_repeats_the_findings() -> None:
    report = analyze(build_corpus(format_by_key("C")))
    payload = json.loads(render_json(report))
    assert payload["tool"] == "binfer"
    assert payload["corpus"]["analyzed"] == 24
    assert len(payload["fields"]) == len(report.fields)
    assert payload["records"][0]["record_size"] == 16
    assert len(payload["records"][0]["fields"]) == len(report.records[0].fields)
    first = payload["fields"][0]
    assert first["evidence"] == {"claim": "identical", "hits": 24, "total": 24}


def test_json_keeps_the_eof_relative_convention() -> None:
    payload = json.loads(render_json(analyze(build_corpus(format_by_key("B")))))
    trailer = [field for field in payload["fields"] if field["offset"] < 0]
    assert len(trailer) == 1
    assert trailer[0]["offset"] == -4


def test_json_is_reproducible() -> None:
    corpus = build_corpus(format_by_key("G"))
    assert render_json(analyze(corpus)) == render_json(analyze(corpus))


def test_ksy_declares_the_magic_as_contents_that_can_be_validated() -> None:
    draft = render_ksy(analyze(build_corpus(format_by_key("C"))), "ctbl")
    assert "id: ctbl" in draft
    assert "contents: [0x43, 0x54, 0x42, 0x4c]" in draft
    assert "type: u4le" in draft


def test_ksy_nests_the_record_as_its_own_type() -> None:
    draft = render_ksy(analyze(build_corpus(format_by_key("C"))))
    assert "types:" in draft
    assert "record_0010:" in draft
    assert draft.count("seq:") == 2


def test_ksy_names_an_interior_hole_explicitly() -> None:
    rng = random.Random(23)
    corpus = Corpus(
        samples=tuple(
            Sample(f"s{index}.bin", b"HEAD" + rng.randbytes(8) + b"TAIL") for index in range(12)
        ),
        discovered=12,
    )
    draft = render_ksy(analyze(corpus))
    assert "- id: unknown_0x0004" in draft
    assert "size: 8" in draft
    assert "doc: not explained by the analysis" in draft


def test_ksy_describes_trailing_padding_as_an_open_ended_span() -> None:
    draft = render_ksy(analyze(build_corpus(format_by_key("A"))))
    assert "size-eos: true" in draft
    assert "zero filled in every sample" in draft


def test_ksy_repeats_the_record_type_to_the_end_of_the_file() -> None:
    draft = render_ksy(analyze(build_corpus(format_by_key("C"))))
    assert "- id: records" in draft
    assert "type: record_0010" in draft
    assert "repeat: eos" in draft


def test_ksy_maps_strings_to_their_encoding() -> None:
    draft = render_ksy(analyze(build_corpus(format_by_key("D"))))
    assert "encoding: ASCII" in draft
    assert "encoding: UTF-16LE" in draft


def test_ksy_leaves_out_what_it_cannot_express() -> None:
    draft = render_ksy(analyze(build_corpus(format_by_key("B"))))
    assert "eof" not in draft.lower()


def test_a_report_with_nothing_in_it_still_renders_all_five_sections() -> None:
    rng = random.Random(22)
    corpus = Corpus(
        samples=tuple(Sample(f"r{index}.bin", rng.randbytes(64)) for index in range(8)),
        discovered=8,
    )
    rendered = render_text(analyze(corpus))
    for section in SECTIONS:
        assert f"\n{section}\n" in f"\n{rendered}"
    assert json.loads(render_json(analyze(corpus)))["fields"] == []


def test_confidence_labels_are_the_three_documented_tiers() -> None:
    rendered = "".join(text_for(fmt.key) for fmt in FORMATS)
    for tier in Confidence:
        assert tier.label in rendered or tier is Confidence.LOW
