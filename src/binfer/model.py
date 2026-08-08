"""Findings, confidence tiers and the assembled report.

Every analysis stage produces instances of these dataclasses and the renderers
consume them, so a renderer never re-derives a fact and never invents one. All
containers are tuples in a defined order: the report must be byte-identical
across runs, which rules out leaking set or dict iteration order outwards.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class Confidence(enum.IntEnum):
    """Three-tier confidence scale, as documented in the README.

    ``PROVED`` holds in every sample and is falsifiable: a checksum matches, or
    length arithmetic closes exactly. ``HIGH`` holds in every sample but rests on
    a statistical argument, such as a type guess. ``LOW`` holds in most samples
    and is reported so the reader can judge it.

    The integer values exist only so overlap resolution can prefer the stronger
    finding with a plain comparison; they are never rendered.
    """

    LOW = 0
    HIGH = 1
    PROVED = 2

    @property
    def label(self) -> str:
        """Return the lowercase tier name used in reports and JSON output."""
        return self.name.lower()

    @classmethod
    def from_label(cls, label: str) -> Confidence:
        """Parse a tier name as accepted by ``--min-confidence``."""
        try:
            return cls[label.upper()]
        except KeyError:
            raise ValueError(f"unknown confidence tier: {label!r}") from None


class Endian(enum.Enum):
    """Byte order of a decoded integer or float field."""

    LITTLE = "le"
    BIG = "be"

    @property
    def struct_prefix(self) -> str:
        """Return the :mod:`struct` format prefix for this byte order."""
        return "<" if self is Endian.LITTLE else ">"


class RegionKind(enum.Enum):
    """Why a span of bytes carries no field."""

    UNEXPLAINED = "unexplained"
    HIGH_ENTROPY = "high-entropy"
    PADDING = "padding"


class RelationKind(enum.Enum):
    """Kind of proved relationship between a field and a span of the file."""

    LENGTH = "length"
    OFFSET = "offset"
    COUNT = "count"
    CHECKSUM = "checksum"


@dataclass(frozen=True, slots=True)
class Evidence:
    """A claim together with the sample count supporting it.

    No finding may exist without one: the report never prints a claim without
    the count behind it.
    """

    claim: str
    hits: int
    total: int

    def render(self) -> str:
        """Format as ``<claim> in <hits>/<total>``."""
        return f"{self.claim} in {self.hits}/{self.total}"

    @property
    def unanimous(self) -> bool:
        """Return whether the claim holds in every sample."""
        return self.hits == self.total


@dataclass(frozen=True, slots=True)
class Field:
    """One typed span of bytes at a fixed offset within a file or a record."""

    offset: int
    size: int
    type_name: str
    value_repr: str
    confidence: Confidence
    evidence: Evidence
    runner_up: str | None = None
    # The bytes themselves, when the field is the same in every sample. Carrying
    # them beats re-parsing ``value_repr``, and the Kaitai export turns them into
    # a ``contents`` assertion that actually validates a file.
    raw: bytes | None = None

    @property
    def end(self) -> int:
        """Return the exclusive end offset."""
        return self.offset + self.size

    def overlaps(self, other: Field) -> bool:
        """Return whether the two fields share at least one byte."""
        return self.offset < other.end and other.offset < self.end

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """Return the total order used for rendering."""
        return (self.offset, self.size, self.type_name)


@dataclass(frozen=True, slots=True)
class Relation:
    """A relationship between a field and a span, proved across the whole corpus."""

    kind: RelationKind
    subject_offset: int
    subject: str
    summary: str
    confidence: Confidence
    evidence: Evidence
    subject_size: int = 0

    @property
    def sort_key(self) -> tuple[str, int, str]:
        """Return the total order used for rendering."""
        return (self.kind.value, self.subject_offset, self.summary)


@dataclass(frozen=True, slots=True)
class Region:
    """A span the analysis could not explain, named as such.

    Offsets follow the same convention as relation subjects: a non-negative
    value counts from the start of the file, a negative one counts back from the
    end, and an ``end`` of zero means the end of the file. A corpus of varying
    sizes has no single absolute offset for its trailer, and pretending
    otherwise would put a wrong number in the report.
    """

    start: int
    end: int
    kind: RegionKind
    entropy: float
    note: str = ""

    @property
    def order(self) -> tuple[int, int]:
        """Return the position of this region in file order."""
        return (0, self.start) if self.start >= 0 else (1, self.start)


@dataclass(frozen=True, slots=True)
class RecordLayout:
    """A repeated fixed-size structure found inside a larger region."""

    start: int
    record_size: int
    count_repr: str
    origin: str
    evidence: Evidence
    fields: tuple[Field, ...] = ()
    relations: tuple[Relation, ...] = ()
    regions: tuple[Region, ...] = ()


@dataclass(frozen=True, slots=True)
class CorpusSummary:
    """Size statistics of the analysed sample set."""

    discovered: int
    analyzed: int
    min_size: int
    max_size: int
    mean_size: float
    median_size: int
    distinct_sizes: int

    @property
    def uniform(self) -> bool:
        """Return whether every analysed sample has the same size."""
        return self.distinct_sizes == 1


@dataclass(frozen=True, slots=True)
class Report:
    """Everything the analysis concluded, ready to render."""

    corpus: CorpusSummary
    fields: tuple[Field, ...] = ()
    relations: tuple[Relation, ...] = ()
    regions: tuple[Region, ...] = ()
    records: tuple[RecordLayout, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


def resolve_overlaps(candidates: Iterable[Field]) -> tuple[Field, ...]:
    """Drop fields that collide with a stronger finding.

    Greedy interval selection: candidates are visited strongest first and kept
    only when disjoint from everything kept so far. Ties break towards wider
    coverage, then towards the lower offset, then by type name, so the result
    does not depend on the order in which the stages happened to emit findings.
    """
    ordered = sorted(
        candidates,
        key=lambda f: (-int(f.confidence), -f.size, f.offset, f.type_name),
    )
    kept: list[Field] = []
    for candidate in ordered:
        if any(candidate.overlaps(other) for other in kept):
            continue
        kept.append(candidate)
    return tuple(sorted(kept, key=lambda f: f.sort_key))
