"""Column statistics across the corpus, and byte entropy inside a single span.

Two different entropy measurements live here and answering the wrong question
with the wrong one is the classic mistake:

* column entropy is measured across samples at one offset and says how much a
  field varies between files;
* byte entropy is measured inside one span of one file and says whether that
  span looks compressed or encrypted.

A four-byte counter has maximal column entropy and very low byte entropy. A
deflate stream is the other way round.
"""

from __future__ import annotations

import enum
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

BYTE_VALUES = 256

# Column entropy is normalised against log2(sample count): a 12-file corpus can
# never exhibit more than 3.58 bits at a single offset, so an absolute
# seven-bits-of-eight test would classify nothing as high entropy at the corpus
# sizes this tool targets.
HIGH_ENTROPY_RATIO = 0.95

# Measured against the most a span of that length could show, because the
# plug-in estimator is biased downwards on short spans: 256 uniformly random
# bytes measure about 7.5 bits, not 8. Deflate output and encrypted payloads
# land above 0.85 of the ceiling, while text, records and pointer tables stay
# below 0.6, so one ratio works without per-format tuning.
BLOB_ENTROPY_RATIO = 0.82

# Below this length the measurement says nothing: the ceiling is log2(n) and the
# bias swamps the signal.
MIN_BLOB_BYTES = 128


class ColumnClass(enum.Enum):
    """Coarse classification of one byte offset across the corpus."""

    CONSTANT = "constant"
    LOW_VARIANCE = "low-variance"
    HIGH_ENTROPY = "high-entropy"


@dataclass(frozen=True, slots=True)
class ColumnStats:
    """Distribution of the byte at one offset over the whole corpus."""

    offset: int
    total: int
    histogram: tuple[tuple[int, int], ...]
    entropy: float

    @property
    def distinct(self) -> int:
        """Return how many different byte values occur at this offset."""
        return len(self.histogram)

    @property
    def values(self) -> tuple[int, ...]:
        """Return the observed byte values, ascending."""
        return tuple(value for value, _ in self.histogram)

    @property
    def constant(self) -> bool:
        """Return whether every sample carries the same byte here."""
        return self.distinct == 1

    @property
    def value(self) -> int | None:
        """Return the single observed value, or ``None`` if the column varies."""
        return self.histogram[0][0] if self.constant else None

    @property
    def varying_mask(self) -> int:
        """Return a mask of the bits that differ between samples."""
        first = self.histogram[0][0]
        mask = 0
        for value, _ in self.histogram:
            mask |= value ^ first
        return mask

    @property
    def normalized_entropy(self) -> float:
        """Return entropy divided by the most this corpus size could show."""
        ceiling = math.log2(min(self.total, BYTE_VALUES))
        return self.entropy / ceiling if ceiling > 0 else 0.0

    @property
    def kind(self) -> ColumnClass:
        """Return the coarse class used to steer the typing stage."""
        if self.constant:
            return ColumnClass.CONSTANT
        if self.normalized_entropy >= HIGH_ENTROPY_RATIO:
            return ColumnClass.HIGH_ENTROPY
        return ColumnClass.LOW_VARIANCE


def shannon_entropy(counts: Iterable[int]) -> float:
    """Return the Shannon entropy in bits of a distribution given as counts.

    The terms are accumulated in sorted order so that the floating-point result
    does not depend on the order the caller produced the counts in; the report
    must be byte-identical between runs.
    """
    ordered = sorted(count for count in counts if count > 0)
    total = sum(ordered)
    if total == 0:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in ordered)


def byte_entropy(data: bytes) -> float:
    """Return the entropy in bits per byte of a single buffer."""
    if not data:
        return 0.0
    return shannon_entropy(Counter(data).values())


def mean_byte_entropy(chunks: Sequence[bytes]) -> float:
    """Return the mean per-byte entropy over the corresponding span of each sample."""
    if not chunks:
        return 0.0
    return statistics.fmean(byte_entropy(chunk) for chunk in chunks)


def entropy_ratio(chunks: Sequence[bytes]) -> float:
    """Return the mean per-byte entropy as a fraction of what the length allows."""
    if not chunks:
        return 0.0
    ceiling = math.log2(min(*(len(chunk) for chunk in chunks), BYTE_VALUES))
    return mean_byte_entropy(chunks) / ceiling if ceiling > 0 else 0.0


def is_high_entropy_span(chunks: Sequence[bytes]) -> bool:
    """Return whether the same span in every sample looks compressed or encrypted."""
    if not chunks or any(len(chunk) < MIN_BLOB_BYTES for chunk in chunks):
        return False
    return entropy_ratio(chunks) >= BLOB_ENTROPY_RATIO


def column_stats(window: Sequence[bytes], *, base_offset: int = 0) -> tuple[ColumnStats, ...]:
    """Compute per-offset statistics over an aligned window.

    Every row must have the same length; a ragged window means the caller
    aligned the corpus wrongly and silently truncating would hide the bug.
    """
    if not window:
        return ()
    total = len(window)
    return tuple(
        ColumnStats(
            offset=base_offset + index,
            total=total,
            histogram=histogram,
            entropy=shannon_entropy(count for _, count in histogram),
        )
        for index, histogram in enumerate(
            tuple(sorted(Counter(column).items())) for column in zip(*window, strict=True)
        )
    )
