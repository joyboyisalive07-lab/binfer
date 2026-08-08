"""Assembly of the whole pipeline into one report.

This is stage six: run the earlier stages, work out which spans nothing
explained, resolve the overlaps between findings and hand the result to a
renderer. Nothing here formats anything and nothing here decides a type.

It lives outside ``report.py`` because that module renders and this one
concludes, and outside ``cli.py`` because the command line is not allowed to
carry analysis.

Offsets follow the report's convention throughout: non-negative counts from the
start of the file, negative counts back from the end, and an end of zero means
the end of the file. A corpus of varying sizes has no single absolute offset for
its trailer.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from binfer.corpus import plan_alignment
from binfer.model import (
    Confidence,
    Field,
    RecordLayout,
    Region,
    RegionKind,
    Report,
    resolve_overlaps,
)
from binfer.records import find_records
from binfer.relations import find_relations
from binfer.stats import (
    BLOB_ENTROPY_RATIO,
    MIN_BLOB_BYTES,
    column_stats,
    entropy_ratio,
    mean_byte_entropy,
)
from binfer.types import MIN_MAGIC_RUN, MIN_PADDING_RUN, TYPING_SCAN_LIMIT, infer_fields

if TYPE_CHECKING:
    from collections.abc import Sequence

    from binfer.corpus import Alignment, Corpus
    from binfer.model import Relation

# A field no wider than one primitive, pressed against a compressed blob with
# nothing but more blob behind it, belongs to the blob. The first bytes of a
# deflate stream are genuinely structured - the zlib header never moves and the
# block header is low-variance - and naming them invents a header that is not
# there.
ABSORB_MAX_FIELD = 8

# Two long samples are enough to call a span compressed; one could be a fluke of
# a single odd file.
MIN_BLOB_SAMPLES = 2


@dataclass(frozen=True, slots=True)
class Options:
    """Everything the command line can change about an analysis."""

    record_size: int | None = None
    min_confidence: Confidence = Confidence.LOW


def _slice(data: bytes, start: int, end: int) -> bytes:
    low = start if start >= 0 else len(data) + start
    high = len(data) if end == 0 else (end if end > 0 else len(data) + end)
    return data[low:high] if high > low else b""


def _chunks(corpus: Corpus, start: int, end: int) -> list[bytes]:
    return [_slice(sample.data, start, end) for sample in corpus.samples]


def _classify(chunks: Sequence[bytes]) -> tuple[RegionKind, float]:
    present = [chunk for chunk in chunks if chunk]
    if not present:
        return RegionKind.UNEXPLAINED, 0.0
    if all(not any(chunk) for chunk in present) and min(map(len, present)) >= MIN_PADDING_RUN:
        return RegionKind.PADDING, 0.0

    entropy = mean_byte_entropy(present)
    measurable = [chunk for chunk in present if len(chunk) >= MIN_BLOB_BYTES]
    if len(measurable) >= MIN_BLOB_SAMPLES and entropy_ratio(measurable) >= BLOB_ENTROPY_RATIO:
        return RegionKind.HIGH_ENTROPY, entropy
    return RegionKind.UNEXPLAINED, entropy


_REGION_NOTES = {
    RegionKind.PADDING: "zero filled in every sample",
    RegionKind.HIGH_ENTROPY: "compressed or encrypted, nothing to recover",
    RegionKind.UNEXPLAINED: "nothing found accounts for these bytes",
}


def _make_region(corpus: Corpus, start: int, end: int) -> Region:
    kind, entropy = _classify(_chunks(corpus, start, end))
    return Region(start=start, end=end, kind=kind, entropy=entropy, note=_REGION_NOTES[kind])


def _gaps(covered: Sequence[bool], base: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, taken in enumerate(covered):
        if not taken and start is None:
            start = index
        elif taken and start is not None:
            spans.append((base + start, base + index))
            start = None
    if start is not None:
        spans.append((base + start, base + len(covered)))
    return spans


def _coverage(fields: Sequence[Field], base: int, width: int) -> list[bool]:
    covered = [False] * width
    for field in fields:
        for index in range(field.offset - base, field.end - base):
            if 0 <= index < width:
                covered[index] = True
    return covered


def _absorb_into_blob(
    corpus: Corpus,
    fields: list[Field],
    base: int,
    width: int,
    protected: frozenset[int],
) -> None:
    """Pull short findings that abut a high-entropy span into that span.

    Removal happens in place. The walk stops at a magic run, at any field a
    relation proved, and at anything wide enough to be a real field, so it can
    only ever eat the handful of bytes at the mouth of a blob.
    """
    gaps = _gaps(_coverage(fields, base, width), base)
    blobs = [
        span for span in gaps if _classify(_chunks(corpus, *span))[0] is RegionKind.HIGH_ENTROPY
    ]
    if not blobs:
        return

    boundary = blobs[0][0]
    while True:
        neighbour = next((item for item in fields if item.end == boundary), None)
        if neighbour is None:
            hole = next((span for span in gaps if span[1] == boundary), None)
            if hole is None or hole[1] - hole[0] > ABSORB_MAX_FIELD:
                return
            boundary = hole[0]
            continue
        if (
            neighbour.offset in protected
            or neighbour.size > ABSORB_MAX_FIELD
            or neighbour.type_name.startswith("magic")
        ):
            return
        fields.remove(neighbour)
        boundary = neighbour.offset


def _merge(regions: Sequence[Region]) -> tuple[Region, ...]:
    merged: list[Region] = []
    for region in sorted(regions, key=lambda item: item.order):
        previous = merged[-1] if merged else None
        if previous is not None and previous.kind is region.kind and previous.end == region.start:
            merged[-1] = dataclasses.replace(previous, end=region.end)
        else:
            merged.append(region)
    return tuple(merged)


def _relation_fields(relations: Sequence[Relation], covered: Sequence[Field]) -> list[Field]:
    """Give every proved relation subject a row in the field table.

    A CRC-32 field is four uniform bytes that the typing stage refuses to name,
    so without this the strongest finding in the file would appear only in the
    relations section and the layout would show a hole where it sits.
    """
    taken = {field.offset for field in covered}
    made: list[Field] = []
    for relation in relations:
        if relation.subject_offset in taken or not relation.subject_size:
            continue
        taken.add(relation.subject_offset)
        made.append(
            Field(
                offset=relation.subject_offset,
                size=relation.subject_size,
                type_name=relation.subject.split()[-1],
                value_repr=f"see {relation.kind.value}",
                confidence=relation.confidence,
                evidence=relation.evidence,
            )
        )
    return made


def _window_fields(corpus: Corpus, alignment: Alignment) -> tuple[list[Field], list[Field]]:
    head = corpus.head_window(alignment.head_size)
    head_fields = list(infer_fields(head, column_stats(head)))

    tail_fields: list[Field] = []
    if alignment.tail_size:
        tail = corpus.tail_window(alignment.tail_size)
        tail_fields = list(infer_fields(tail, column_stats(tail, base_offset=-alignment.tail_size)))
    return head_fields, tail_fields


def _notes(
    corpus: Corpus,
    alignment: Alignment,
    relation_notes: Sequence[str],
    regions: Sequence[Region],
    hidden: int,
) -> tuple[str, ...]:
    notes = list(corpus.warnings)
    if alignment.tail_size:
        notes.append(
            f"sizes differ, so offsets were compared over a {alignment.head_size}-byte head window "
            f"and a {alignment.tail_size}-byte tail window; the span between them has no common "
            "offset and was measured but not aligned"
        )
    if alignment.head_size > TYPING_SCAN_LIMIT:
        notes.append(
            f"the numeric scan covered the first {TYPING_SCAN_LIMIT} bytes of each window; "
            "constant runs and strings were still scanned in full"
        )
    notes.extend(relation_notes)
    if any(region.kind is RegionKind.HIGH_ENTROPY for region in regions):
        notes.append(
            "a high-entropy region is reported as unexplained on purpose; compressed or "
            "encrypted bytes carry no structure that a corpus of samples can recover"
        )
    if hidden:
        notes.append(f"{hidden} finding(s) below the requested confidence were not shown")
    return tuple(notes)


def analyze(corpus: Corpus, options: Options | None = None) -> Report:
    """Run every stage and assemble the findings into a report."""
    settings = options or Options()
    alignment = plan_alignment(corpus)

    head_fields, tail_fields = _window_fields(corpus, alignment)
    relation_result = find_relations(corpus, alignment)
    records = find_records(corpus, relation_result.record_hints, record_size=settings.record_size)

    protected = frozenset(relation.subject_offset for relation in relation_result.relations)
    if records:
        # A record array runs to the end of the file, so everything from its
        # start onwards belongs to the nested table, and nothing the head or
        # tail window typed inside it is a field of its own.
        first = min(layout.start for layout in records)
        head_fields = [field for field in head_fields if field.end <= first]
        tail_fields = []

    _absorb_into_blob(corpus, head_fields, 0, alignment.head_size, protected)
    if tail_fields:
        if _classify(_chunks(corpus, -alignment.tail_size, 0))[0] is RegionKind.HIGH_ENTROPY:
            # The whole trailer is compressed. Anything typed inside it is a
            # regularity of the compressor, not a field of the format.
            tail_fields = [field for field in tail_fields if field.offset in protected]
        else:
            _absorb_into_blob(
                corpus, tail_fields, -alignment.tail_size, alignment.tail_size, protected
            )

    fields = [*head_fields, *tail_fields]
    fields.extend(_relation_fields(relation_result.relations, fields))
    resolved = resolve_overlaps(fields)

    regions = _collect_regions(corpus, alignment, resolved, records)
    tier = settings.min_confidence
    shown = tuple(field for field in resolved if field.confidence >= tier)
    relations = tuple(
        relation for relation in relation_result.relations if relation.confidence >= tier
    )
    hidden = len(resolved) - len(shown)

    # The nested record table is part of the layout and has to obey the same
    # threshold; filtering only the top level would show a hidden tier anyway.
    filtered_records = []
    for layout in records:
        kept = tuple(field for field in layout.fields if field.confidence >= tier)
        hidden += len(layout.fields) - len(kept)
        filtered_records.append(
            dataclasses.replace(
                layout,
                fields=kept,
                relations=tuple(
                    relation for relation in layout.relations if relation.confidence >= tier
                ),
            )
        )
    records = tuple(filtered_records)

    return Report(
        corpus=corpus.summary(),
        fields=shown,
        relations=relations,
        regions=regions,
        records=records,
        notes=_notes(corpus, alignment, relation_result.notes, regions, hidden),
    )


def _collect_regions(
    corpus: Corpus,
    alignment: Alignment,
    fields: Sequence[Field],
    records: Sequence[RecordLayout],
) -> tuple[Region, ...]:
    covered = _coverage([field for field in fields if field.offset >= 0], 0, alignment.head_size)
    for layout in records:
        for index in range(layout.start, alignment.head_size):
            covered[index] = True

    spans = list(_gaps(covered, 0))
    if alignment.tail_size and not records:
        tail = [field for field in fields if field.offset < 0]
        spans.append((alignment.head_size, -alignment.tail_size))
        spans.extend(
            _gaps(_coverage(tail, -alignment.tail_size, alignment.tail_size), -alignment.tail_size)
        )

    regions = [_make_region(corpus, start, end) for start, end in spans if start != end]
    if not alignment.tail_size:
        # The head window is the whole file, so a gap reaching its end reaches
        # the end of the file and should say so.
        regions = [
            dataclasses.replace(region, end=0) if region.end == alignment.head_size else region
            for region in regions
        ]
    return _merge(regions)


def region_covers_records(regions: Sequence[Region]) -> bool:
    """Return whether any region was classified as compressed or encrypted."""
    return any(region.kind is RegionKind.HIGH_ENTROPY for region in regions)


MAGIC_RUN = MIN_MAGIC_RUN
