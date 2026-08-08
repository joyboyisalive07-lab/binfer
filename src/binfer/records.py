"""Repeated-record segmentation, and recursion of the earlier stages into a record.

The record size is never guessed. It comes from a count field whose arithmetic
the relation stage proved exactly, or from ``--record-size`` when the caller
already knows it. A blind search over plausible strides is not attempted: no
synthetic format here would demonstrate that such a search works.

Where the records start is inferred, because the arithmetic does not say: a
count relation reads ``value * k + c == file size`` and cannot tell whether the
``c`` bytes sit in front of the records, behind them, or both. Every start that
divides the region exactly is tried, and the one whose pooled records have the
lowest mean column entropy wins. At the true start each field lands in the same
column of every record; at any other, the fields smear and the columns look
random.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

from binfer.corpus import Corpus, Sample, plan_alignment
from binfer.model import Evidence, RecordLayout
from binfer.relations import find_relations
from binfer.stats import column_stats
from binfer.types import infer_fields

if TYPE_CHECKING:
    from collections.abc import Sequence

    from binfer.relations import RecordHint

# A one-byte record is not a record, it is a byte array.
MIN_RECORD_SIZE = 2

# Records begin after a header, and headers are small. Searching further turns
# the start search into a second blind stride search by the back door.
MAX_RECORD_START = 256

# Below this many pooled records the column statistics of the record body say
# nothing, and a layout built on them would be noise dressed as a finding.
MIN_POOLED_RECORDS = 8


@dataclass(frozen=True, slots=True)
class Segmentation:
    """Where the records start, how wide they are and how many each sample holds."""

    start: int
    stride: int
    counts: tuple[int, ...]
    entropy: float

    @property
    def total_records(self) -> int:
        """Return how many records the whole corpus contributes."""
        return sum(self.counts)

    @property
    def count_repr(self) -> str:
        """Return the per-sample record count as printed in the report."""
        low, high = min(self.counts), max(self.counts)
        return str(low) if low == high else f"{low}..{high}"


def pool_records(corpus: Corpus, start: int, stride: int) -> tuple[bytes, ...]:
    """Return every record of every sample as one flat sequence of equal rows."""
    pooled: list[bytes] = []
    for sample in corpus.samples:
        for index in range((sample.size - start) // stride):
            offset = start + index * stride
            pooled.append(sample.data[offset : offset + stride])
    return tuple(pooled)


def _mean_column_entropy(rows: Sequence[bytes]) -> float:
    columns = column_stats(rows)
    if not columns:
        return 1.0
    return statistics.fmean(column.normalized_entropy for column in columns)


def _candidate_starts(corpus: Corpus, stride: int) -> list[int]:
    limit = min(MAX_RECORD_START, corpus.min_size - stride)
    return [
        start
        for start in range(limit + 1)
        if all((sample.size - start) % stride == 0 for sample in corpus.samples)
        and sum((sample.size - start) // stride for sample in corpus.samples) >= MIN_POOLED_RECORDS
    ]


def segment(corpus: Corpus, stride: int) -> Segmentation | None:
    """Choose where records of ``stride`` bytes begin, or give up."""
    if stride < MIN_RECORD_SIZE or corpus.min_size <= stride:
        return None

    scored: list[Segmentation] = []
    for start in _candidate_starts(corpus, stride):
        pooled = pool_records(corpus, start, stride)
        scored.append(
            Segmentation(
                start=start,
                stride=stride,
                counts=tuple((sample.size - start) // stride for sample in corpus.samples),
                entropy=_mean_column_entropy(pooled),
            )
        )
    if not scored:
        return None
    return min(scored, key=lambda item: (item.entropy, item.start))


def analyse_records(corpus: Corpus, segmentation: Segmentation, origin: str) -> RecordLayout | None:
    """Recurse the column, typing and relation stages into the record body.

    Returns ``None`` when nothing inside the record can be typed: a segmentation
    that explains no field is not evidence of a record layout, and reporting it
    would be padding the report with a table of unknowns.
    """
    pooled = pool_records(corpus, segmentation.start, segmentation.stride)
    if len(pooled) < MIN_POOLED_RECORDS:
        return None

    fields = infer_fields(pooled, column_stats(pooled))
    if not fields:
        return None

    inner = Corpus(
        samples=tuple(Sample(f"record_{index:05d}", data) for index, data in enumerate(pooled)),
        discovered=len(pooled),
    )
    relations = find_relations(inner, plan_alignment(inner)).relations

    total = corpus.count
    return RecordLayout(
        start=segmentation.start,
        record_size=segmentation.stride,
        count_repr=segmentation.count_repr,
        origin=origin,
        evidence=Evidence(f"{segmentation.start:#06x}..EOF divides exactly", total, total),
        fields=fields,
        relations=relations,
    )


def find_records(
    corpus: Corpus,
    hints: Sequence[RecordHint],
    *,
    record_size: int | None = None,
) -> tuple[RecordLayout, ...]:
    """Segment and describe every record array the corpus evidences."""
    wanted: list[tuple[int, str]] = []
    if record_size is not None:
        wanted.append((record_size, f"--record-size {record_size}"))
    else:
        wanted.extend((hint.stride, f"count field at {hint.subject}") for hint in hints)

    layouts: list[RecordLayout] = []
    for stride, origin in wanted:
        segmentation = segment(corpus, stride)
        if segmentation is None:
            continue
        layout = analyse_records(corpus, segmentation, origin)
        if layout is not None and all(other.start != layout.start for other in layouts):
            layouts.append(layout)
    return tuple(sorted(layouts, key=lambda layout: (layout.start, layout.record_size)))
