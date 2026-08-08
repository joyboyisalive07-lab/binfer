"""Renderers: the text report, the JSON findings and the Kaitai Struct draft.

Nothing here concludes anything. Every number printed was decided by an earlier
stage, which is what makes it possible to say that the report never carries a
claim without the count behind it: the count travels with the claim.

The text report is laid out for a hundred-column terminal and for pasting into
a forum post, so it uses no box drawing and no colour beyond the confidence
column, which can be turned off.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from binfer import __version__
from binfer.model import Confidence, RegionKind

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from binfer.model import CorpusSummary, Field, RecordLayout, Region, Relation, Report
    from binfer.synth import Scorecard

WIDTH = 100
OFFSET_COLUMN = 9
SIZE_COLUMN = 5
TYPE_COLUMN = 16
VALUE_COLUMN = 18
CONFIDENCE_COLUMN = 11
SUBJECT_COLUMN = 15
KIND_COLUMN = 9
SUMMARY_COLUMN = 37
SPAN_COLUMN = 19
REGION_KIND_COLUMN = 14
ENTROPY_COLUMN = 16

_COLOURS = {
    Confidence.PROVED: "\x1b[32m",
    Confidence.HIGH: "\x1b[36m",
    Confidence.LOW: "\x1b[33m",
}
_RESET = "\x1b[0m"


def position(offset: int) -> str:
    """Render an offset in the report's start-or-EOF convention."""
    if offset == 0:
        return "0x0000"
    return f"0x{offset:04X}" if offset > 0 else f"EOF-{-offset}"


def _span(start: int, end: int) -> str:
    return f"{position(start)}..{'EOF' if end == 0 else position(end)}"


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "~"


def _tier(confidence: Confidence, *, colour: bool) -> str:
    label = confidence.label.ljust(CONFIDENCE_COLUMN)
    return f"{_COLOURS[confidence]}{label}{_RESET}" if colour else label


def _field_row(field: Field, prefix: str, *, colour: bool) -> str:
    offset = (prefix + position(field.offset)).ljust(OFFSET_COLUMN)
    # The runner-up rides in the type column so the evidence count, which the
    # report may never drop, keeps the whole line to the right of it.
    reading = f"{field.type_name} / {field.runner_up}" if field.runner_up else field.type_name
    return (
        f"  {offset}"
        f"{str(field.size).rjust(SIZE_COLUMN)}  "
        f"{_clip(reading, TYPE_COLUMN).ljust(TYPE_COLUMN)}"
        f"{_clip(field.value_repr, VALUE_COLUMN).ljust(VALUE_COLUMN)}"
        f"{_tier(field.confidence, colour=colour)}"
        f"{field.evidence.render()}"
    ).rstrip()


def _table_header(prefix: str = "") -> str:
    return (
        f"  {(prefix + 'OFFSET').ljust(OFFSET_COLUMN)}"
        f"{'SIZE'.rjust(SIZE_COLUMN)}  "
        f"{'TYPE'.ljust(TYPE_COLUMN)}"
        f"{'VALUE/RANGE'.ljust(VALUE_COLUMN)}"
        f"{'CONFIDENCE'.ljust(CONFIDENCE_COLUMN)}"
        "EVIDENCE"
    )


def _corpus_lines(summary: CorpusSummary) -> list[str]:
    lines = ["CORPUS"]
    counted = f"  {summary.analyzed} files analysed"
    if summary.discovered != summary.analyzed:
        counted += f" of {summary.discovered} found"
    lines.append(counted)
    if summary.uniform:
        lines.append(f"  every file is {summary.min_size} bytes; sizes are uniform")
    else:
        lines.append(
            f"  {summary.min_size}..{summary.max_size} bytes, "
            f"mean {summary.mean_size:.0f}, median {summary.median_size}"
        )
        lines.append(f"  sizes are not uniform, {summary.distinct_sizes} distinct sizes")
    return lines


def _layout_lines(
    fields: Sequence[Field],
    records: Sequence[RecordLayout],
    *,
    colour: bool,
) -> list[str]:
    lines = ["LAYOUT"]
    if not fields and not records:
        lines.append("  nothing in this corpus could be typed")
        return lines

    if fields:
        lines.append(_table_header())
        lines.extend(_field_row(field, "", colour=colour) for field in fields)

    for layout in records:
        lines.append("")
        lines.append(
            f"  records at {position(layout.start)}, {layout.record_size} bytes each, "
            f"{layout.count_repr} per file  ({layout.origin})"
        )
        lines.append(f"  {layout.evidence.render()}")
        lines.append(_table_header("+"))
        lines.extend(_field_row(field, "+", colour=colour) for field in layout.fields)
        lines.extend(
            f"  +{position(relation.subject_offset)} {relation.kind.value}: {relation.summary}"
            for relation in layout.relations
        )
    return lines


def _relation_lines(relations: Sequence[Relation], *, colour: bool) -> list[str]:
    lines = ["RELATIONS"]
    if not relations:
        lines.append("  none proved")
        return lines
    lines.extend(
        f"  {_clip(relation.subject, SUBJECT_COLUMN).ljust(SUBJECT_COLUMN)}"
        f"{relation.kind.value.ljust(KIND_COLUMN)}"
        f"{_clip(relation.summary, SUMMARY_COLUMN).ljust(SUMMARY_COLUMN)}"
        f"{_tier(relation.confidence, colour=colour)}"
        f"{relation.evidence.render()}"
        for relation in relations
    )
    return lines


def _region_lines(regions: Sequence[Region]) -> list[str]:
    lines = ["REGIONS"]
    if not regions:
        lines.append("  every byte is accounted for")
        return lines
    for region in regions:
        entropy = f"{region.entropy:.2f} bits/byte" if region.kind is not RegionKind.PADDING else ""
        lines.append(
            f"  {_span(region.start, region.end).ljust(SPAN_COLUMN)}"
            f"{region.kind.value.ljust(REGION_KIND_COLUMN)}"
            f"{entropy.ljust(ENTROPY_COLUMN)}{region.note}"
        )
    return lines


def _note_lines(notes: Sequence[str]) -> list[str]:
    lines = ["NOTES"]
    if not notes:
        lines.append("  none")
        return lines
    for note in notes:
        lines.extend(_wrap(f"  - {note}", "    "))
    return lines


def _wrap(text: str, continuation: str) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    indent = len(text) - len(text.lstrip())
    lines: list[str] = []
    current = " " * indent + words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) > WIDTH:
            lines.append(current)
            current = continuation + word
        else:
            current += " " + word
    lines.append(current)
    return lines


def render_text(report: Report, *, colour: bool = False) -> str:
    """Render the primary human-readable report."""
    blocks = [
        _corpus_lines(report.corpus),
        _layout_lines(report.fields, report.records, colour=colour),
        _relation_lines(report.relations, colour=colour),
        _region_lines(report.regions),
        _note_lines(report.notes),
    ]
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def _tally(pair: tuple[int, int]) -> str:
    return "-" if pair[1] == 0 else f"{pair[0]}/{pair[1]}"


def render_scorecard(cards: Sequence[Scorecard], *, colour: bool = False) -> str:
    """Render the recovery scorecard printed by ``--self-test``."""
    lines = [
        "SELF TEST",
        f"  binfer {__version__}, {len(cards)} synthetic formats with declared ground truth",
        "",
        "  KEY  FORMAT            FIELDS  RELATIONS  RECORDS  OPAQUE  RESULT",
    ]
    for card in cards:
        verdict = "pass" if card.passed else "FAIL"
        tinted = (
            f"{_COLOURS[Confidence.PROVED]}{verdict}{_RESET}" if colour and card.passed else verdict
        )
        lines.append(
            f"  {card.key.ljust(5)}{card.name.ljust(18)}"
            f"{_tally(card.fields).ljust(8)}"
            f"{_tally(card.relations).ljust(11)}"
            f"{_tally(card.record_fields).ljust(9)}"
            f"{('ok' if card.opaque_ok else 'no').ljust(8)}{tinted}"
        )

    failed = [card for card in cards if not card.passed]
    lines.append("")
    for card in failed:
        for problem in card.problems:
            lines.extend(_wrap(f"  {card.key}: {problem}", "     "))
    lines.append(f"  {len(cards) - len(failed)} of {len(cards)} formats fully recovered")
    return "\n".join(lines) + "\n"


def _field_json(field: Field) -> dict[str, object]:
    return {
        "offset": field.offset,
        "size": field.size,
        "type": field.type_name,
        "value": field.value_repr,
        "confidence": field.confidence.label,
        "evidence": {
            "claim": field.evidence.claim,
            "hits": field.evidence.hits,
            "total": field.evidence.total,
        },
        "runner_up": field.runner_up,
    }


def _relation_json(relation: Relation) -> dict[str, object]:
    return {
        "kind": relation.kind.value,
        "subject": relation.subject,
        "offset": relation.subject_offset,
        "size": relation.subject_size,
        "summary": relation.summary,
        "confidence": relation.confidence.label,
        "evidence": {
            "claim": relation.evidence.claim,
            "hits": relation.evidence.hits,
            "total": relation.evidence.total,
        },
    }


def render_json(report: Report) -> str:
    """Render the machine-readable findings.

    Offsets keep the report's convention: negative counts back from the end of
    the file and an ``end`` of zero means the end of the file.
    """
    payload = {
        "tool": "binfer",
        "version": __version__,
        "corpus": {
            "discovered": report.corpus.discovered,
            "analyzed": report.corpus.analyzed,
            "min_size": report.corpus.min_size,
            "max_size": report.corpus.max_size,
            "mean_size": round(report.corpus.mean_size, 2),
            "median_size": report.corpus.median_size,
            "distinct_sizes": report.corpus.distinct_sizes,
            "uniform": report.corpus.uniform,
        },
        "fields": [_field_json(field) for field in report.fields],
        "relations": [_relation_json(relation) for relation in report.relations],
        "regions": [
            {
                "start": region.start,
                "end": region.end,
                "kind": region.kind.value,
                "entropy": round(region.entropy, 4),
                "note": region.note,
            }
            for region in report.regions
        ],
        "records": [
            {
                "start": layout.start,
                "record_size": layout.record_size,
                "count": layout.count_repr,
                "origin": layout.origin,
                "fields": [_field_json(field) for field in layout.fields],
                "relations": [_relation_json(relation) for relation in layout.relations],
            }
            for layout in report.records
        ],
        "notes": list(report.notes),
    }
    return json.dumps(payload, indent=2) + "\n"


_KAITAI_TYPES = {
    "u8": "u1",
    "i8": "s1",
    "u16le": "u2le",
    "u16be": "u2be",
    "i16le": "s2le",
    "i16be": "s2be",
    "u32le": "u4le",
    "u32be": "u4be",
    "i32le": "s4le",
    "i32be": "s4be",
    "u64le": "u8le",
    "u64be": "u8be",
    "i64le": "s8le",
    "i64be": "s8be",
    "f32le": "f4le",
    "f32be": "f4be",
    "f64le": "f8le",
    "f64be": "f8be",
    "enum8": "u1",
    "enum16le": "u2le",
    "enum16be": "u2be",
    "bits8": "u1",
    "unix32le": "u4le",
    "unix32be": "u4be",
    "unixms64le": "u8le",
    "unixms64be": "u8be",
    "filetime64le": "u8le",
    "filetime64be": "u8be",
    "ticks64le": "u8le",
    "ticks64be": "u8be",
}


def _kaitai_attribute(field: Field, index: int) -> list[str]:
    name = f"field_{index:02d}_{position(field.offset).lower().replace('-', '_')}"
    lines = [f"  - id: {name}"]
    mapped = _KAITAI_TYPES.get(field.type_name)
    if field.raw is not None:
        contents = ", ".join(f"0x{byte:02x}" for byte in field.raw)
        lines.append(f"    contents: [{contents}]")
    elif mapped:
        lines.append(f"    type: {mapped}")
    elif field.type_name.startswith("ascii"):
        lines.extend([f"    size: {field.size}", "    type: str", "    encoding: ASCII"])
    elif field.type_name.startswith("utf16le"):
        lines.extend([f"    size: {field.size}", "    type: str", "    encoding: UTF-16LE"])
    else:
        lines.append(f"    size: {field.size}")
    lines.append(f"    doc: {field.type_name}, {field.confidence.label}, {field.evidence.render()}")
    return lines


def _kaitai_gap(start: int, index: int, size: int | None, doc: str) -> list[str]:
    lines = [f"  - id: unknown_{index:02d}_{position(start).lower().replace('-', '_')}"]
    lines.append(f"    size: {size}" if size is not None else "    size-eos: true")
    lines.append(f"    doc: {doc}")
    return lines


def _kaitai_sequence(fields: Iterable[Field], start: int) -> list[str]:
    lines: list[str] = []
    cursor = start
    for index, field in enumerate(fields):
        if field.offset > cursor:
            lines.extend(
                _kaitai_gap(cursor, index, field.offset - cursor, "not explained by the analysis")
            )
        lines.extend(_kaitai_attribute(field, index))
        cursor = field.end
    return lines


def _kaitai_tail(report: Report, cursor: int) -> list[str]:
    """Describe whatever follows the last field the draft could place."""
    if report.records:
        layout = report.records[0]
        return [
            "  - id: records",
            f"    type: record_{layout.start:04x}",
            "    repeat: eos",
            f"    doc: {layout.origin}, {layout.evidence.render()}",
        ]

    trailing = next(
        (region for region in report.regions if region.start >= cursor and region.end <= 0),
        None,
    )
    if trailing is None:
        return []
    doc = trailing.note
    if trailing.end < 0:
        doc += (
            f"; the last {-trailing.end} bytes are described in the report but need an expression"
        )
    return _kaitai_gap(trailing.start, 99, None, doc)


def render_ksy(report: Report, format_id: str = "unknown_format") -> str:
    """Render a Kaitai Struct draft.

    It is a draft on purpose. Only fields at a fixed offset from the start of
    the file become attributes; every hole between them is named explicitly, and
    anything the analysis anchored to the end of the file is left as an
    open-ended span, since Kaitai cannot express a trailer without a size
    expression the corpus does not supply.
    """
    head = [field for field in report.fields if field.offset >= 0]
    lines = [
        "meta:",
        f"  id: {format_id}",
        "  endian: le",
        "doc: |",
        "  Draft produced by binfer from a corpus of samples. Every attribute",
        "  carries the evidence that supports it; unknown_* attributes are spans",
        "  the analysis could not explain and are not padding unless said so.",
        "seq:",
    ]
    body = _kaitai_sequence(head, 0)
    body.extend(_kaitai_tail(report, head[-1].end if head else 0))
    lines.extend(body or ["  []"])

    for layout in report.records:
        lines.extend(["types:", f"  record_{layout.start:04x}:", "    seq:"])
        lines.extend("  " + line for line in _kaitai_sequence(layout.fields, 0))
    return "\n".join(lines) + "\n"
