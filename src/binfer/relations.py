"""Relationships between a field and a span, proved across the whole corpus.

Nothing here reports a correlation. A length relation is accepted only when
integer ``k`` and ``c`` exist such that ``value * k + c`` equals the file size in
every single sample; a checksum only when the stored value equals the computed
one everywhere. Two dozen exact hits cannot happen by accident, which is why
these findings carry the proved tier while a type guess never can.

The scan does not read the typing stage's output. A checksum field is four
uniform bytes and the typing stage deliberately refuses to name it, so relying
on named fields would lose exactly the case this stage exists for. Spans are
enumerated directly instead.
"""

from __future__ import annotations

import functools
import operator
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from binfer.model import Confidence, Endian, Evidence, Relation, RelationKind
from binfer.stats import BYTE_VALUES
from binfer.types import MIN_DISTINCT_VALUES, PRINTABLE

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from binfer.corpus import Alignment, Corpus

BITS_PER_BYTE = 8
CANDIDATE_SIZES = (1, 2, 4, 8)
CRC16_MASK = 0xFFFF

# Length and pointer fields live in headers and trailers. Scanning further costs
# a decode per offset per sample and buys nothing.
RELATION_SCAN_LIMIT = 1024

# A stride wider than this is not a record size, it is a coincidence. 4096 is
# already larger than any fixed record the tool has been tested against.
MAX_STRIDE = 4096

# A pointer must land on at least this many printable bytes followed by a NUL
# before the landing site counts as a string.
MIN_POINTER_STRING = 3

# Checksums sit in the header or the trailer, never in the middle.
CHECKSUM_WINDOW = 64

# Hashing budget for the whole checksum search, in bytes. Each candidate span
# costs roughly three passes over one sample, so this bounds the search on a
# corpus of large files instead of letting it run for minutes.
CHECKSUM_BUDGET_BYTES = 256 * 1024 * 1024
MIN_CHECKSUM_SPANS = 8

# The CRC-16 variants are implemented here rather than pulled in as a
# dependency, which makes them pure Python and roughly a hundred times slower
# than zlib's CRC-32. Past this sample size they are skipped and the report says
# so; zlib still covers CRC-32 and Adler-32 at any size.
CRC16_MAX_SAMPLE_BYTES = 256 * 1024


def _reflected_table(poly: int) -> tuple[int, ...]:
    """Build a least-significant-bit-first CRC-16 table for a reversed polynomial."""
    table = []
    for value in range(BYTE_VALUES):
        crc = value
        for _ in range(BITS_PER_BYTE):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


def _forward_table(poly: int) -> tuple[int, ...]:
    """Build a most-significant-bit-first CRC-16 table."""
    table = []
    for value in range(BYTE_VALUES):
        crc = (value << BITS_PER_BYTE) & CRC16_MASK
        for _ in range(BITS_PER_BYTE):
            crc = ((crc << 1) ^ poly) & CRC16_MASK if crc & 0x8000 else (crc << 1) & CRC16_MASK
        table.append(crc)
    return tuple(table)


# 0xA001 is 0x8005 reversed, the polynomial behind CRC-16/ARC and CRC-16/MODBUS.
_REFLECTED = _reflected_table(0xA001)
# 0x1021 is the CCITT polynomial, behind CRC-16/CCITT-FALSE and CRC-16/XMODEM.
_FORWARD = _forward_table(0x1021)


def crc16_reflected(data: bytes, init: int) -> int:
    """Compute a reflected CRC-16 over the 0x8005 polynomial."""
    crc = init
    for byte in data:
        crc = (crc >> BITS_PER_BYTE) ^ _REFLECTED[(crc ^ byte) & 0xFF]
    return crc


def crc16_forward(data: bytes, init: int) -> int:
    """Compute a most-significant-bit-first CRC-16 over the 0x1021 polynomial."""
    crc = init
    for byte in data:
        crc = ((crc << BITS_PER_BYTE) & CRC16_MASK) ^ _FORWARD[
            ((crc >> BITS_PER_BYTE) ^ byte) & 0xFF
        ]
    return crc


@dataclass(frozen=True, slots=True)
class Algorithm:
    """One checksum the search knows how to compute."""

    name: str
    widths: frozenset[int]
    compute: Callable[[bytes], int]
    pure_python: bool = False


ALGORITHMS: tuple[Algorithm, ...] = (
    Algorithm("crc32", frozenset({4}), zlib.crc32),
    Algorithm("adler32", frozenset({4}), zlib.adler32),
    Algorithm(
        "crc16-arc", frozenset({2}), lambda data: crc16_reflected(data, 0x0000), pure_python=True
    ),
    Algorithm(
        "crc16-modbus", frozenset({2}), lambda data: crc16_reflected(data, 0xFFFF), pure_python=True
    ),
    Algorithm(
        "crc16-ccitt", frozenset({2}), lambda data: crc16_forward(data, 0xFFFF), pure_python=True
    ),
    Algorithm(
        "crc16-xmodem", frozenset({2}), lambda data: crc16_forward(data, 0x0000), pure_python=True
    ),
    Algorithm("sum", frozenset({1, 2, 4, 8}), sum),
    Algorithm("xor", frozenset({1}), lambda data: functools.reduce(operator.xor, data, 0)),
)

RANGES: tuple[tuple[str, Callable[[bytes, int, int], bytes]], ...] = (
    ("everything before the field", lambda data, start, _end: data[:start]),
    ("from the field to EOF", lambda data, _start, end: data[end:]),
    (
        "the whole file except the field",
        lambda data, start, end: data[:start] + data[end:],
    ),
)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A span read as an integer, together with its value in every sample."""

    label: str
    anchor: int
    size: int
    type_name: str
    starts: tuple[int, ...]
    values: tuple[int, ...]

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """Return the total order used when several candidates fit."""
        return (self.anchor, -self.size, self.type_name)

    def subject(self) -> str:
        """Return the position and reading, as printed in the report."""
        return f"{self.label} {self.type_name}"


@dataclass(frozen=True, slots=True)
class RelationResult:
    """Everything the relation stage concluded."""

    relations: tuple[Relation, ...] = ()
    notes: tuple[str, ...] = ()
    strides: tuple[int, ...] = ()


def _decode(data: bytes, start: int, size: int, endian: Endian) -> int:
    order = "little" if endian is Endian.LITTLE else "big"
    return int.from_bytes(data[start : start + size], order)


def _candidates_at(
    corpus: Corpus,
    *,
    from_end: bool,
    limit: int,
) -> list[Candidate]:
    found: list[Candidate] = []
    for size in CANDIDATE_SIZES:
        endians = (Endian.LITTLE,) if size == 1 else (Endian.LITTLE, Endian.BIG)
        for distance in range(limit - size + 1):
            if from_end:
                starts = tuple(sample.size - distance - size for sample in corpus.samples)
                label = f"EOF-{distance + size}"
                anchor = -(distance + size)
            else:
                starts = tuple(distance for _ in corpus.samples)
                label = f"0x{distance:04X}"
                anchor = distance
            if any(start < 0 for start in starts):
                continue
            for endian in endians:
                values = tuple(
                    _decode(sample.data, start, size, endian)
                    for sample, start in zip(corpus.samples, starts, strict=True)
                )
                if len(set(values)) < MIN_DISTINCT_VALUES:
                    # A constant span cannot pin down k in a length fit, cannot
                    # be a varying pointer, and would make a checksum claim that
                    # holds only because nothing ever moved.
                    continue
                suffix = "" if size == 1 else endian.value
                found.append(
                    Candidate(
                        label=label,
                        anchor=anchor,
                        size=size,
                        type_name=f"u{size * BITS_PER_BYTE}{suffix}",
                        starts=starts,
                        values=values,
                    )
                )
    return found


def span_candidates(corpus: Corpus, alignment: Alignment) -> tuple[Candidate, ...]:
    """Enumerate every integer reading in the head and tail windows.

    A uniform corpus has no tail window because absolute offsets already reach
    the end of the file, and scanning from both ends would report the same field
    twice under two different names.
    """
    found = _candidates_at(
        corpus, from_end=False, limit=min(alignment.head_size, RELATION_SCAN_LIMIT)
    )
    found.extend(
        _candidates_at(corpus, from_end=True, limit=min(alignment.tail_size, RELATION_SCAN_LIMIT))
    )
    return tuple(sorted(found, key=lambda candidate: candidate.sort_key))


def _preference(candidate: Candidate) -> tuple[bool, int, int, str]:
    """Order candidates so the best-aligned, widest reading is considered first.

    Alignment outranks width. A pointer at 0x38 also reads as an eight-byte
    big-endian value starting at 0x31 whenever the seven bytes in front of it
    happen to be zero, and that reading fits every test the real field does.
    Only its alignment gives it away.
    """
    return (
        abs(candidate.anchor) % candidate.size != 0,
        -candidate.size,
        candidate.anchor,
        candidate.type_name,
    )


def _overlaps(left: Candidate, right: Candidate) -> bool:
    return any(
        first < second + right.size and second < first + left.size
        for first, second in zip(left.starts, right.starts, strict=True)
    )


def fit_linear(values: Sequence[int], targets: Sequence[int]) -> tuple[int, int] | None:
    """Fit integer ``k`` and ``c`` with ``value * k + c == target`` in every sample.

    Two samples determine the pair and the rest either confirm it or kill it.
    Nothing approximate is accepted: a single mismatch rejects the fit.
    """
    low = min(range(len(values)), key=lambda index: values[index])
    high = max(range(len(values)), key=lambda index: values[index])
    span = values[high] - values[low]
    if span == 0:
        return None
    rise = targets[high] - targets[low]
    if rise % span:
        return None
    stride = rise // span
    if not 1 <= stride <= MAX_STRIDE:
        return None
    constant = targets[low] - stride * values[low]
    if constant < 0:
        return None
    if any(
        value * stride + constant != target for value, target in zip(values, targets, strict=True)
    ):
        return None
    return stride, constant


def _length_summary(stride: int, constant: int) -> str:
    scaled = "value" if stride == 1 else f"value * {stride}"
    if constant == 0:
        return f"{scaled} == file size"
    return f"{scaled} + {constant} == file size"


def find_length_relations(
    corpus: Corpus,
    candidates: Sequence[Candidate],
) -> tuple[tuple[Relation, ...], tuple[int, ...]]:
    """Find fields whose value scales exactly to the file size."""
    sizes = list(corpus.sizes)
    total = corpus.count
    fitted: list[tuple[Candidate, int, int]] = []
    for candidate in candidates:
        fit = fit_linear(candidate.values, sizes)
        if fit is not None:
            fitted.append((candidate, *fit))

    relations: list[Relation] = []
    strides: list[int] = []
    kept: list[Candidate] = []
    for candidate, stride, constant in sorted(fitted, key=lambda item: _preference(item[0])):
        if any(_overlaps(other, candidate) for other in kept):
            # A four-byte length fits, and so does its low half, and so does a
            # misaligned reading that straddles it. Reporting all three says the
            # same thing three times.
            continue
        kept.append(candidate)
        strides.append(stride)
        relations.append(
            Relation(
                kind=RelationKind.LENGTH if stride == 1 else RelationKind.COUNT,
                subject_offset=candidate.anchor,
                subject=candidate.subject(),
                summary=(
                    _length_summary(stride, constant)
                    if stride == 1
                    else f"{_length_summary(stride, constant)}, so {stride}-byte records"
                ),
                confidence=Confidence.PROVED,
                evidence=Evidence("holds exactly", total, total),
            )
        )
    return tuple(relations), tuple(sorted(set(strides) - {1}))


def _string_starts_at(data: bytes, position: int) -> bool:
    end = position
    while end < len(data) and data[end] in PRINTABLE:
        end += 1
    return end - position >= MIN_POINTER_STRING and end < len(data) and data[end] == 0


def find_offset_relations(
    corpus: Corpus,
    candidates: Sequence[Candidate],
) -> tuple[Relation, ...]:
    """Find fields whose value is an in-file position where a string begins."""
    total = corpus.count
    relations: list[Relation] = []
    kept: list[Candidate] = []
    for candidate in sorted(candidates, key=_preference):
        pairs = list(zip(corpus.samples, candidate.values, strict=True))
        if any(not 0 < value < sample.size for sample, value in pairs):
            continue
        if any(not _string_starts_at(sample.data, value) for sample, value in pairs):
            continue
        if any(_overlaps(other, candidate) for other in kept):
            continue
        kept.append(candidate)
        relations.append(
            Relation(
                kind=RelationKind.OFFSET,
                subject_offset=candidate.anchor,
                subject=candidate.subject(),
                summary="points at a NUL-terminated ASCII string",
                confidence=Confidence.HIGH,
                evidence=Evidence("lands on a string", total, total),
            )
        )
    return tuple(relations)


def checksum_pool(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Return the spans worth hashing, trailers first."""
    pool = [candidate for candidate in candidates if abs(candidate.anchor) <= CHECKSUM_WINDOW]
    # Trailers first, nearest EOF first, then headers from the front: that is
    # where checksums actually live, and the budget may cut the tail off.
    return sorted(pool, key=lambda item: (item.anchor >= 0, abs(item.anchor), item.sort_key))


def find_checksum_relations(
    corpus: Corpus,
    candidates: Sequence[Candidate],
) -> tuple[tuple[Relation, ...], tuple[str, ...]]:
    """Find fields holding a checksum of some span of the same file."""
    pool = checksum_pool(candidates)
    notes: list[str] = []

    allowed = max(MIN_CHECKSUM_SPANS, CHECKSUM_BUDGET_BYTES // max(1, 3 * corpus.max_size))
    if len(pool) > allowed:
        notes.append(
            f"checksum search covered {allowed} of {len(pool)} candidate spans; "
            "the samples are large enough that hashing every span would dominate the run"
        )
        pool = pool[:allowed]

    algorithms = ALGORITHMS
    if corpus.max_size > CRC16_MAX_SAMPLE_BYTES:
        algorithms = tuple(item for item in ALGORITHMS if not item.pure_python)
        notes.append(
            "CRC-16 variants were skipped: they are implemented here in pure Python to "
            "avoid a dependency, and these samples are too large for that to be quick"
        )

    total = corpus.count
    relations: list[Relation] = []
    for candidate in pool:
        found = _match_checksum(corpus, candidate, algorithms)
        if found is None:
            continue
        algorithm, range_name = found
        relations.append(
            Relation(
                kind=RelationKind.CHECKSUM,
                subject_offset=candidate.anchor,
                subject=candidate.subject(),
                summary=f"{algorithm} of {range_name}",
                confidence=Confidence.PROVED,
                evidence=Evidence("matches", total, total),
            )
        )
    return tuple(relations), tuple(notes)


def _match_checksum(
    corpus: Corpus,
    candidate: Candidate,
    algorithms: Sequence[Algorithm],
) -> tuple[str, str] | None:
    mask = (1 << (candidate.size * BITS_PER_BYTE)) - 1
    first = corpus.samples[0]
    first_start = candidate.starts[0]
    for algorithm in algorithms:
        if candidate.size not in algorithm.widths:
            continue
        for range_name, slice_of in RANGES:
            probe = slice_of(first.data, first_start, first_start + candidate.size)
            if not probe or algorithm.compute(probe) & mask != candidate.values[0]:
                # Checking one sample first turns a hopeless span into a single
                # pass instead of twenty-four.
                continue
            if all(
                algorithm.compute(slice_of(sample.data, start, start + candidate.size)) & mask
                == value
                for sample, start, value in zip(
                    corpus.samples, candidate.starts, candidate.values, strict=True
                )
            ):
                return algorithm.name, range_name
    return None


def find_relations(corpus: Corpus, alignment: Alignment) -> RelationResult:
    """Run every relation search and return the findings in a fixed order."""
    candidates = span_candidates(corpus, alignment)
    lengths, strides = find_length_relations(corpus, candidates)
    offsets = find_offset_relations(corpus, candidates)
    checksums, notes = find_checksum_relations(corpus, candidates)

    relations = tuple(
        sorted((*lengths, *offsets, *checksums), key=lambda relation: relation.sort_key)
    )
    return RelationResult(relations=relations, notes=notes, strides=strides)
