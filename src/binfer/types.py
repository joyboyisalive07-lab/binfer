"""Typed hypotheses over groups of adjacent columns, and the rules that score them.

The hard part of typing a binary field is not proposing readings, it is refusing
the ones nothing supports. Four uniformly random bytes decode perfectly well as
a u32 and nothing in the bytes says otherwise. So an integer hypothesis is
emitted only when the corpus carries positive evidence for it: constant zero
high bytes, a high byte that never approaches 0xFF, byte entropy that falls
towards the significant end, or a high byte confined to the two neighbourhoods
of 0x00 and 0xFF that a signed value produces. Without one of those the span is
left unexplained, which is the honest answer and the reason a compressed blob
does not fill the field table with invented integers.

Selection runs in two passes. Typed hypotheses compete first, greedily by score.
Constant columns that nothing claimed then become magic and const fields,
computed over what is left, so an integer that legitimately extends into two
constant zero bytes never has to out-argue a run of constants that only partly
overlaps it.

Indices inside this module are window-local. Absolute offsets appear only on
``ColumnStats`` and on the findings handed back to the caller.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
import struct
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from binfer.model import Confidence, Endian, Evidence, Field
from binfer.stats import BYTE_VALUES

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from binfer.stats import ColumnStats

# Seconds between the FILETIME epoch (1601-01-01) and the unix epoch.
FILETIME_EPOCH_OFFSET = 11_644_473_600
# Seconds between the .NET DateTime epoch (0001-01-01) and the unix epoch.
TICKS_EPOCH_OFFSET = 62_135_596_800
# Both encodings count 100-nanosecond intervals.
HUNDRED_NS_PER_SECOND = 10_000_000
MILLISECONDS_PER_SECOND = 1000

# 1990-01-01 and 2041-01-01. A timestamp is accepted only when every sample
# lands inside this band, which uniformly random bytes clear about four times
# in 10^11 across a corpus of two dozen files.
TIMESTAMP_MIN = 631_152_000
TIMESTAMP_MAX = 2_240_524_800

BYTE_BITS = 8
SINGLE_BYTE = 1
F32_SIZE = 4
F64_SIZE = 8
UTF16_UNIT = 2
# A field that decodes to one value everywhere is a constant, not a number.
MIN_DISTINCT_VALUES = 2

INT_SIZES = (1, 2, 4, 8)
FLOAT_SIZES = (F32_SIZE, F64_SIZE)
ENUM_SIZES = (1, 2)

# The numeric scan costs about thirty readings per offset, which is affordable
# for a header and not for a 64 KiB window. Past this many bytes from the start
# of a window the numeric scan stops and the report says so.
TYPING_SCAN_LIMIT = 4096

# An integer hypothesis needs this much support from its strongest criterion.
# 0.45 corresponds to a high byte that never exceeds 0x8C, which 24 uniformly
# random samples clear about twice in 10^7.
INTEGER_SUPPORT_FLOOR = 0.45

# A high byte reads as a sign extension only when every observed value sits in
# one of the two neighbourhoods of 0x00 and 0xFF.
SIGN_EDGE = 0x0F

# Real float corpora occupy a narrow exponent band. Uniformly random bytes
# spread the f32 exponent over roughly 250 values and the f64 exponent over
# roughly 2000, so these limits separate the cases without per-format tuning.
# 40 and 60 still admit a dynamic range of 2**40 and 2**60 within one field.
F32_EXPONENT_SPREAD_LIMIT = 40
F64_EXPONENT_SPREAD_LIMIT = 60
F32_MANTISSA_BITS = 23
F64_MANTISSA_BITS = 52
F32_EXPONENT_BITS = 8
F64_EXPONENT_BITS = 11

# Bytes belonging to some other field routinely decode as a float of absurd
# magnitude: 1e-305, 1e+274. Quantities a program actually stores in a file sit
# far inside this band, and the band is what rejects those readings.
FLOAT_MIN_MAGNITUDE = 1e-9
FLOAT_MAX_MAGNITUDE = 1e15

# An enum is a closed set: few values, every one of them seen more than once.
ENUM_MAX_VALUES = 8
ENUM_MIN_SAMPLES_PER_VALUE = 2

# A bitfield claim is falsifiable only when every combination of the varying
# bits has actually been observed, which needs at least 2**bits samples; past
# six bits that is more samples than a corpus normally holds. Below three bits
# the claim says nothing a four-valued enum does not already say.
BITFIELD_MIN_BITS = 3
BITFIELD_MAX_BITS = 6

MIN_ASCII_FIELD = 4
MIN_UTF16_FIELD = 8
# One printable byte followed by NUL padding is not a string, it is the low byte
# of a small integer. Three characters is the shortest field worth the claim.
MIN_ASCII_TEXT = 3
MIN_UTF16_UNITS = 2
MIN_MAGIC_RUN = 4
# A constant all-zero run this long is padding, not a field. It stays uncovered
# so the region stage can name it for what it is.
MIN_PADDING_RUN = 4
# A string field that holds in most but not all samples is still worth showing,
# at the low tier.
STRING_LOW_RATIO = 0.8

# A timestamp outranks everything: twenty-four samples all landing in one
# fifty-year window is the most specific claim this stage can make, and the
# hypothesis is discarded outright unless it holds in every sample.
TIMESTAMP_SCORE = 1.00
STRING_SCORE = 0.99
BITFIELD_SCORE = 0.92
ENUM_SCORE = 0.90
FLOAT_SCORE = 0.90

# Weights of the five integer criteria. They sum to one, so an integer score is
# directly comparable with the flat scores above.
W_HIGH_BYTE = 0.40
W_GRADIENT = 0.15
W_SLOPE = 0.20
W_WASTE = 0.10
W_SIGN = 0.15

# Nearly every format aligns a multi-byte field to its own width, and a reading
# that starts mid-field is the commonest false positive in a dense offset scan.
# The bonus is large enough to break a tie and too small to rescue a hypothesis
# with no evidence behind it.
W_ALIGNMENT = 0.15

# Scores this close together are treated as equal, and the tie goes to whichever
# hypothesis explains more bytes. A rule covering thirty-two bytes is a better
# account of a file than one covering two, even when both fit their span.
SCORE_BUCKET = 0.05

# Two or more constant non-zero bytes inside one field are the signature of a
# magic, not of a clock: a zlib header reads as a plausible big-endian unix time
# in every sample because its high bytes never move. Constant zero high bytes
# are a different matter and stay allowed, since real 64-bit clocks have them.
TIMESTAMP_MAX_CONSTANT_BYTES = 1

# A runner-up is worth printing only when it was nearly as good.
RUNNER_UP_MARGIN = 0.08

PRINTABLE = frozenset(range(0x20, 0x7F))
PRINTABLE_OR_NUL = PRINTABLE | {0}

_INT_CODE = {1: "B", 2: "H", 4: "I", 8: "Q"}
_FLOAT_CODE = {F32_SIZE: "f", F64_SIZE: "d"}
_TIMESTAMP_KINDS = (
    ("unix32", F32_SIZE),
    ("unixms64", F64_SIZE),
    ("filetime64", F64_SIZE),
    ("ticks64", F64_SIZE),
)


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """One candidate reading of a span, with the score that ranks it."""

    offset: int
    size: int
    type_name: str
    score: float
    confidence: Confidence
    value_repr: str
    evidence: Evidence

    @property
    def end(self) -> int:
        """Return the exclusive end offset."""
        return self.offset + self.size

    def as_field(self, runner_up: str | None = None) -> Field:
        """Convert to a reported field."""
        return Field(
            offset=self.offset,
            size=self.size,
            type_name=self.type_name,
            value_repr=self.value_repr,
            confidence=self.confidence,
            evidence=self.evidence,
            runner_up=runner_up,
        )


@dataclass(frozen=True, slots=True)
class _Window:
    """An aligned window: the sample rows and the statistics of their columns."""

    rows: Sequence[bytes]
    columns: Sequence[ColumnStats]

    @property
    def base(self) -> int:
        return self.columns[0].offset

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.columns)

    @property
    def ceiling(self) -> float:
        """Return the largest entropy one column could show at this corpus size."""
        return math.log2(min(self.total, BYTE_VALUES))

    def evidence(self, claim: str) -> Evidence:
        return Evidence(claim, self.total, self.total)


def _reader(code: str, endian: Endian) -> struct.Struct:
    return struct.Struct(endian.struct_prefix + code)


def _blend(base: float, *, aligned: bool) -> float:
    """Fold the alignment bonus into a criterion score."""
    return (1.0 - W_ALIGNMENT) * base + (W_ALIGNMENT if aligned else 0.0)


def _significance_order(index: int, size: int, endian: Endian) -> tuple[int, ...]:
    """Return window-local indices from the least to the most significant byte."""
    positions = tuple(range(index, index + size))
    return positions if endian is Endian.LITTLE else positions[::-1]


def _slope_score(entropies: Sequence[float], ceiling: float) -> float:
    """Penalise byte entropy that grows towards the most significant end.

    In a real integer the high-order bytes take fewer distinct values than the
    low-order ones. Random bytes show no such gradient, and reading a big-endian
    field as little-endian inverts it, which is what separates the two.
    """
    if len(entropies) < MIN_DISTINCT_VALUES or ceiling <= 0:
        return 1.0
    growth = sum(max(0.0, later - earlier) for earlier, later in itertools.pairwise(entropies))
    return max(0.0, 1.0 - growth / ((len(entropies) - 1) * ceiling))


def _waste_score(used_bits: int, size: int, zero_pad: int) -> float:
    """Penalise width that neither the values nor a constant zero run explains."""
    wasted = (size * BYTE_BITS - used_bits) / BYTE_BITS
    return max(0.0, 1.0 - max(0.0, wasted - zero_pad) / size)


def _zero_pad(window: _Window, order: Sequence[int]) -> int:
    """Count most-significant columns that are constant zero in every sample."""
    pad = 0
    for index in reversed(order):
        column = window.columns[index]
        if not column.constant or column.value != 0:
            break
        pad += 1
    return pad


def _is_bimodal_sign(values: Sequence[int]) -> bool:
    low = high = False
    for value in values:
        if value <= SIGN_EDGE:
            low = True
        elif value >= BYTE_VALUES - 1 - SIGN_EDGE:
            high = True
        else:
            return False
    return low and high


def _int_name(size: int, endian: Endian, *, signed: bool) -> str:
    prefix = "i" if signed else "u"
    bits = size * BYTE_BITS
    return f"{prefix}{bits}" if size == SINGLE_BYTE else f"{prefix}{bits}{endian.value}"


def _read_ints(
    window: _Window, index: int, size: int, endian: Endian, *, signed: bool
) -> list[int]:
    reader = _reader(_INT_CODE[size], endian)
    values = [reader.unpack_from(row, index)[0] for row in window.rows]
    if not signed:
        return values
    span = 1 << (size * BYTE_BITS)
    limit = span >> 1
    return [value - span if value >= limit else value for value in values]


def _integer_values(
    window: _Window,
    index: int,
    size: int,
    endian: Endian,
    *,
    signed: bool,
) -> list[int] | None:
    """Return the decoded values, or ``None`` when the span cannot be this field."""
    order = _significance_order(index, size, endian)
    if not window.columns[order[0]].varying_mask:
        # A field whose least significant byte never moves is not a counter, a
        # length or an identifier; it is a constant that happens to sit here.
        return None

    zero_pad = _zero_pad(window, order)
    significant = order[: size - zero_pad]
    if any(window.columns[position].value == 0 for position in significant[:-1]):
        # A dead byte in the middle of a live integer is the signature of two
        # adjacent fields read as one, not of a single wide value.
        return None

    values = _read_ints(window, index, size, endian, signed=signed)
    if len(set(values)) < MIN_DISTINCT_VALUES:
        return None
    if signed and all(value >= 0 for value in values):
        # The unsigned reading already explains these bytes.
        return None
    return values


def _integer_hypothesis(
    window: _Window,
    index: int,
    size: int,
    endian: Endian,
    *,
    signed: bool,
) -> Hypothesis | None:
    values = _integer_values(window, index, size, endian, signed=signed)
    if values is None:
        return None

    order = _significance_order(index, size, endian)
    span = [window.columns[position] for position in order]
    entropies = [column.entropy for column in span]
    msb_max = max(span[-1].values)
    ceiling = window.ceiling

    zero_pad = _zero_pad(window, order)
    pad_score = 1.0 if zero_pad else 0.0
    headroom = max(0.0, 1.0 - msb_max / (BYTE_VALUES - 1))
    gradient = max(0.0, (entropies[0] - entropies[-1]) / ceiling) if ceiling else 0.0
    bimodal = _is_bimodal_sign(span[-1].values)
    sign_support = 1.0 if signed and bimodal else 0.0

    support, reason = max(
        (
            (pad_score, f"top {zero_pad} byte(s) constant zero"),
            (headroom, f"high byte never above 0x{msb_max:02X}"),
            (gradient, "byte entropy falls towards the high end"),
            (sign_support, "high byte only near 0x00 and 0xFF"),
        ),
        key=lambda item: item[0],
    )
    if support < INTEGER_SUPPORT_FLOOR:
        return None

    used_bits = max(abs(value) for value in values).bit_length() + (1 if signed else 0)
    score = (
        W_HIGH_BYTE * max(pad_score, headroom)
        + W_GRADIENT * gradient
        + W_SLOPE * _slope_score(entropies, ceiling)
        + W_WASTE * _waste_score(used_bits, size, zero_pad)
        + W_SIGN * (1.0 if signed == bimodal else 0.0)
    )
    offset = window.base + index
    return Hypothesis(
        offset=offset,
        size=size,
        type_name=_int_name(size, endian, signed=signed),
        score=_blend(score, aligned=offset % size == 0),
        confidence=Confidence.HIGH,
        value_repr=f"{min(values)}..{max(values)}",
        evidence=window.evidence(reason),
    )


def _exponent_spread(values: Sequence[float], size: int) -> int | None:
    """Return the width of the exponent band, or ``None`` if the reading is invalid.

    NaN, infinity and non-zero denormals are what random bytes produce and real
    float corpora do not, so any of them rejects the hypothesis outright. Exact
    zero is common in real data and is simply skipped.
    """
    mantissa_bits = F32_MANTISSA_BITS if size == F32_SIZE else F64_MANTISSA_BITS
    exponent_bits = F32_EXPONENT_BITS if size == F32_SIZE else F64_EXPONENT_BITS
    mask = (1 << exponent_bits) - 1
    packer = struct.Struct("<f" if size == F32_SIZE else "<d")
    seen: list[int] = []
    for value in values:
        raw = int.from_bytes(packer.pack(value), "little")
        exponent = (raw >> mantissa_bits) & mask
        if exponent == mask:
            return None
        if exponent == 0:
            if raw & ((1 << mantissa_bits) - 1):
                return None
            continue
        seen.append(exponent)
    return max(seen) - min(seen) if seen else None


def _float_hypothesis(
    window: _Window,
    index: int,
    size: int,
    endian: Endian,
) -> Hypothesis | None:
    order = _significance_order(index, size, endian)
    if not window.columns[order[0]].varying_mask:
        return None

    reader = _reader(_FLOAT_CODE[size], endian)
    values = [reader.unpack_from(row, index)[0] for row in window.rows]
    if len(set(values)) < MIN_DISTINCT_VALUES:
        return None

    spread = _exponent_spread(values, size)
    limit = F32_EXPONENT_SPREAD_LIMIT if size == F32_SIZE else F64_EXPONENT_SPREAD_LIMIT
    if spread is None or spread > limit:
        return None
    if any(
        not FLOAT_MIN_MAGNITUDE <= abs(value) <= FLOAT_MAX_MAGNITUDE
        for value in values
        if value != 0.0
    ):
        return None

    offset = window.base + index
    return Hypothesis(
        offset=offset,
        size=size,
        type_name=f"f{size * BYTE_BITS}{endian.value}",
        score=_blend(FLOAT_SCORE, aligned=offset % size == 0),
        confidence=Confidence.HIGH,
        value_repr=f"{min(values):.6g}..{max(values):.6g}",
        evidence=window.evidence(f"finite, exponent band {spread} wide"),
    )


def _timestamp_seconds(value: int, kind: str) -> float:
    if kind == "unix32":
        return float(value)
    if kind == "unixms64":
        return value / MILLISECONDS_PER_SECOND
    if kind == "filetime64":
        return value / HUNDRED_NS_PER_SECOND - FILETIME_EPOCH_OFFSET
    return value / HUNDRED_NS_PER_SECOND - TICKS_EPOCH_OFFSET


def _timestamp_is_plausible(window: _Window, order: Sequence[int]) -> bool:
    if not window.columns[order[0]].varying_mask:
        return False
    if all(set(window.columns[position].values) <= PRINTABLE_OR_NUL for position in order):
        # Four lowercase letters decode to 2021-2035 as a unix32, so any run of
        # ASCII text passes the band test. The string reading is the right one.
        return False
    constant_non_zero = sum(
        1
        for position in order
        if window.columns[position].constant and window.columns[position].value
    )
    return constant_non_zero <= TIMESTAMP_MAX_CONSTANT_BYTES


def _timestamp_hypothesis(
    window: _Window,
    index: int,
    kind: str,
    size: int,
    endian: Endian,
) -> Hypothesis | None:
    order = _significance_order(index, size, endian)
    if not _timestamp_is_plausible(window, order):
        return None
    if _float_hypothesis(window, index, size, endian) is not None:
        # A float32 holding 0..100 also decodes as a unix32 in 2002-2005. The
        # float test is the more constrained of the two - finite, narrow
        # exponent band, plausible magnitude - so it takes the span.
        return None

    raw = _read_ints(window, index, size, endian, signed=False)
    if len(set(raw)) < MIN_DISTINCT_VALUES:
        return None

    seconds = [_timestamp_seconds(value, kind) for value in raw]
    if any(not TIMESTAMP_MIN <= value < TIMESTAMP_MAX for value in seconds):
        return None

    years = [dt.datetime.fromtimestamp(value, tz=dt.UTC).year for value in seconds]
    band = f"{min(years)}-{max(years)}"
    offset = window.base + index
    return Hypothesis(
        offset=offset,
        size=size,
        type_name=f"{kind}{endian.value}",
        score=_blend(TIMESTAMP_SCORE, aligned=offset % size == 0),
        confidence=Confidence.HIGH,
        value_repr=band,
        evidence=window.evidence(f"decodes to {band}"),
    )


def _enum_hypothesis(
    window: _Window,
    index: int,
    size: int,
    endian: Endian,
) -> Hypothesis | None:
    order = _significance_order(index, size, endian)
    span = [window.columns[position] for position in order]
    if not span[0].varying_mask:
        return None
    if any(column.varying_mask for column in span[1:]):
        # Only the least significant byte may move. Otherwise the value set is
        # not a small tag, and nothing would support the chosen byte order.
        return None
    if _is_bimodal_sign(span[0].values):
        # Values clustered at both ends of the byte are a sign extension, not a
        # tag. A genuine {0, 255} flag loses here and is reported as an integer.
        return None

    counts = Counter(_read_ints(window, index, size, endian, signed=False))
    values = sorted(counts)
    if not MIN_DISTINCT_VALUES <= len(values) <= ENUM_MAX_VALUES:
        return None
    if any(count < ENUM_MIN_SAMPLES_PER_VALUE for count in counts.values()):
        # A value seen exactly once is an outlier, not a member of a closed set.
        # Without this, a corpus holding two odd files turns every column into
        # an enum of "the usual value, plus whatever those two files carry".
        return None

    bits = size * BYTE_BITS
    offset = window.base + index
    return Hypothesis(
        offset=offset,
        size=size,
        type_name=f"enum{bits}" if size == SINGLE_BYTE else f"enum{bits}{endian.value}",
        score=_blend(ENUM_SCORE, aligned=offset % size == 0),
        confidence=Confidence.HIGH,
        value_repr="{" + ", ".join(str(value) for value in values) + "}",
        evidence=window.evidence(f"only {len(values)} distinct values"),
    )


def _bitfield_hypothesis(column: ColumnStats) -> Hypothesis | None:
    mask = column.varying_mask
    bits = mask.bit_count()
    if not BITFIELD_MIN_BITS <= bits <= BITFIELD_MAX_BITS:
        return None
    if column.distinct != 1 << bits:
        # Short of every combination the claim is an enum in disguise, and
        # nothing in the corpus could falsify it.
        return None

    positions = ",".join(str(bit) for bit in range(BYTE_BITS) if mask >> bit & 1)
    total = column.total
    return Hypothesis(
        offset=column.offset,
        size=SINGLE_BYTE,
        type_name="bits8",
        score=_blend(BITFIELD_SCORE, aligned=True),
        confidence=Confidence.HIGH,
        value_repr=f"bits {positions}",
        evidence=Evidence(f"all {1 << bits} combinations of {bits} bits seen", total, total),
    )


def _ascii_run(row: bytes, start: int) -> int:
    index = start
    while index < len(row) and row[index] in PRINTABLE:
        index += 1
    if index - start < MIN_ASCII_TEXT:
        return 0
    while index < len(row) and row[index] == 0:
        index += 1
    return index - start


def _utf16_run(row: bytes, start: int) -> int:
    index = start
    while index + 1 < len(row) and row[index] in PRINTABLE and row[index + 1] == 0:
        index += UTF16_UNIT
    if (index - start) // UTF16_UNIT < MIN_UTF16_UNITS:
        return 0
    while index + 1 < len(row) and row[index] == 0 and row[index + 1] == 0:
        index += UTF16_UNIT
    return index - start


def _string_hypothesis(window: _Window, index: int, *, utf16: bool) -> Hypothesis | None:
    measure = _utf16_run if utf16 else _ascii_run
    minimum = MIN_UTF16_FIELD if utf16 else MIN_ASCII_FIELD
    lengths = sorted(measure(row, index) for row in window.rows)
    total = len(lengths)

    if lengths[0] >= minimum:
        length, hits, confidence = lengths[0], total, Confidence.HIGH
    else:
        length = lengths[total - max(1, math.ceil(STRING_LOW_RATIO * total))]
        if length < minimum:
            return None
        hits = sum(1 for value in lengths if value >= length)
        confidence = Confidence.LOW

    if not any(window.columns[index + step].varying_mask for step in range(length)):
        return None

    encoding = "utf-16-le" if utf16 else "ascii"
    texts = sorted(
        {
            row[index : index + length].rstrip(b"\x00").decode(encoding, "replace")
            for row in window.rows
        }
    )
    offset = window.base + index
    return Hypothesis(
        offset=offset,
        size=length,
        type_name=f"{'utf16le' if utf16 else 'ascii'}[{length}]",
        score=_blend(STRING_SCORE, aligned=not utf16 or offset % UTF16_UNIT == 0),
        confidence=confidence,
        value_repr=f"{len(texts)} distinct, e.g. {texts[0]!r}",
        evidence=Evidence("printable then NUL padding", hits, total),
    )


def _integer_and_enum_hypotheses(window: _Window, index: int) -> Iterator[Hypothesis]:
    for size in INT_SIZES:
        if index + size > window.width:
            return
        endians = (Endian.LITTLE,) if size == SINGLE_BYTE else (Endian.LITTLE, Endian.BIG)
        for endian in endians:
            for signed in (False, True):
                found = _integer_hypothesis(window, index, size, endian, signed=signed)
                if found is not None:
                    yield found
            if size in ENUM_SIZES:
                found = _enum_hypothesis(window, index, size, endian)
                if found is not None:
                    yield found


def _real_and_time_hypotheses(window: _Window, index: int) -> Iterator[Hypothesis]:
    for size in FLOAT_SIZES:
        if index + size > window.width:
            break
        for endian in (Endian.LITTLE, Endian.BIG):
            found = _float_hypothesis(window, index, size, endian)
            if found is not None:
                yield found

    for kind, size in _TIMESTAMP_KINDS:
        if index + size > window.width:
            continue
        for endian in (Endian.LITTLE, Endian.BIG):
            found = _timestamp_hypothesis(window, index, kind, size, endian)
            if found is not None:
                yield found


def _numeric_hypotheses(window: _Window, index: int) -> Iterator[Hypothesis]:
    bitfield = _bitfield_hypothesis(window.columns[index])
    if bitfield is not None:
        yield bitfield
    yield from _integer_and_enum_hypotheses(window, index)
    yield from _real_and_time_hypotheses(window, index)


def _constant_run_mask(
    columns: Sequence[ColumnStats],
    predicate: Callable[[int], bool],
    minimum: int,
    skip_first: int = 0,
) -> list[bool]:
    def qualifies(column: ColumnStats) -> bool:
        return column.constant and column.value is not None and predicate(column.value)

    mask = [False] * len(columns)
    index = 0
    while index < len(columns):
        if qualifies(columns[index]):
            run = index
            while run < len(columns) and qualifies(columns[run]):
                run += 1
            if run - index >= minimum:
                for position in range(index + skip_first, run):
                    mask[position] = True
            index = run
        else:
            index += 1
    return mask


def _anchors(columns: Sequence[ColumnStats]) -> tuple[list[bool], list[bool]]:
    """Return spans nothing may straddle, and spans only numerics may not.

    A constant printable run of at least four bytes is the most reliable
    structural marker a binary format has; letting any reading cross one
    destroys the anchor and shifts every field behind it.

    A constant all-zero run of at least four bytes is padding. An integer may
    still take its first byte as a high byte - a four-byte field holding a
    three-byte value looks exactly like that next to padding - but nothing may
    reach further in. Strings are exempt, because the NUL tail of a fixed-width
    string is the same pattern and belongs to the string.
    """
    hard = _constant_run_mask(columns, PRINTABLE.__contains__, MIN_MAGIC_RUN)
    padding = _constant_run_mask(columns, lambda value: value == 0, MIN_PADDING_RUN, skip_first=1)
    return hard, padding


def _all_hypotheses(window: _Window, scan_limit: int) -> list[Hypothesis]:
    hard, padding = _anchors(window.columns)
    base = window.base

    def allowed(candidate: Hypothesis, mask: Sequence[bool]) -> bool:
        return not any(
            mask[position] for position in range(candidate.offset - base, candidate.end - base)
        )

    found: list[Hypothesis] = []
    for index in range(window.width):
        if index < scan_limit:
            found.extend(
                candidate
                for candidate in _numeric_hypotheses(window, index)
                if allowed(candidate, padding)
            )
        for utf16 in (False, True):
            text = _string_hypothesis(window, index, utf16=utf16)
            if text is not None:
                found.append(text)
    return [candidate for candidate in found if allowed(candidate, hard)]


def _bucket(score: float) -> int:
    return math.floor(score / SCORE_BUCKET)


def _select(hypotheses: Sequence[Hypothesis]) -> list[Hypothesis]:
    ordered = sorted(
        hypotheses,
        key=lambda h: (
            -int(h.confidence),
            -_bucket(h.score),
            -h.size,
            -h.score,
            h.offset,
            h.type_name,
        ),
    )
    kept: list[Hypothesis] = []
    for candidate in ordered:
        if any(candidate.offset < other.end and other.offset < candidate.end for other in kept):
            continue
        kept.append(candidate)
    return kept


def _runner_up(chosen: Hypothesis, hypotheses: Sequence[Hypothesis]) -> str | None:
    rivals = sorted(
        (
            other
            for other in hypotheses
            if other.offset == chosen.offset
            and other.type_name != chosen.type_name
            and chosen.score - other.score <= RUNNER_UP_MARGIN
        ),
        key=lambda h: (-h.score, h.type_name),
    )
    return rivals[0].type_name if rivals else None


def _magic_pieces(
    columns: Sequence[ColumnStats],
    start: int,
    length: int,
) -> list[tuple[int, int, bool]]:
    """Split a constant run into printable magic pieces and everything else."""
    end = start + length
    magic: list[tuple[int, int]] = []
    index = start
    while index < end:
        if columns[index].value in PRINTABLE:
            run = index
            while run < end and columns[run].value in PRINTABLE:
                run += 1
            if run - index >= MIN_MAGIC_RUN:
                magic.append((index, run - index))
            index = run
        else:
            index += 1

    pieces: list[tuple[int, int, bool]] = []
    cursor = start
    for magic_start, magic_length in magic:
        if magic_start > cursor:
            pieces.append((cursor, magic_start - cursor, False))
        pieces.append((magic_start, magic_length, True))
        cursor = magic_start + magic_length
    if cursor < end:
        pieces.append((cursor, end - cursor, False))
    return pieces


def _constant_runs(
    columns: Sequence[ColumnStats], covered: Sequence[bool]
) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, column in enumerate(columns):
        if column.constant and not covered[index]:
            if start is None:
                start = index
        elif start is not None:
            runs.append((start, index - start))
            start = None
    if start is not None:
        runs.append((start, len(columns) - start))
    return runs


def _constant_fields(window: _Window, covered: Sequence[bool]) -> list[Field]:
    columns = window.columns
    fields: list[Field] = []
    for start, length in _constant_runs(columns, covered):
        for piece_start, piece_length, printable in _magic_pieces(columns, start, length):
            data = bytes(columns[piece_start + step].value or 0 for step in range(piece_length))
            if not printable and not any(data) and piece_length >= MIN_PADDING_RUN:
                continue
            fields.append(
                Field(
                    offset=columns[piece_start].offset,
                    size=piece_length,
                    type_name=f"{'magic' if printable else 'const'}[{piece_length}]",
                    value_repr=repr(data.decode("ascii")) if printable else data.hex(" "),
                    confidence=Confidence.HIGH,
                    evidence=window.evidence("identical"),
                )
            )
    return fields


def infer_fields(
    window: Sequence[bytes],
    columns: Sequence[ColumnStats],
    *,
    scan_limit: int = TYPING_SCAN_LIMIT,
) -> tuple[Field, ...]:
    """Type an aligned window and return the fields that survived selection.

    ``columns`` must carry absolute offsets and correspond position by position
    to the bytes of every row in ``window``.
    """
    if not window or not columns:
        return ()

    view = _Window(rows=window, columns=columns)
    hypotheses = _all_hypotheses(view, scan_limit)
    chosen = _select(hypotheses)

    covered = [False] * len(columns)
    for hypothesis in chosen:
        for index in range(hypothesis.offset - view.base, hypothesis.end - view.base):
            covered[index] = True

    fields = [candidate.as_field(_runner_up(candidate, hypotheses)) for candidate in chosen]
    fields.extend(_constant_fields(view, covered))
    return tuple(sorted(fields, key=lambda item: item.sort_key))
