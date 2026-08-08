"""Synthetic corpora with declared ground truth.

Correctness of an inference tool cannot be argued from a real file whose layout
nobody knows. Every claim binfer makes is instead checked against formats built
here, where the schema is written down before the bytes exist.

The declared type names are the vocabulary the typing stage emits. Where an
encoding is genuinely ambiguous - a two-byte value followed by two constant zero
bytes really can be read either way - the ground truth lists the alternatives it
accepts rather than pretending one reading is the only correct one.

This module lives in the package rather than in ``tools/`` because
``binfer --self-test`` has to work from a pip install and from the frozen
executable, neither of which ships ``tools/``.
"""

from __future__ import annotations

import random
import struct
import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from binfer.analyze import analyze
from binfer.corpus import Corpus, Sample
from binfer.model import RegionKind, RelationKind
from binfer.types import FILETIME_EPOCH_OFFSET, HUNDRED_NS_PER_SECOND, TICKS_EPOCH_OFFSET

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from binfer.model import Field

SAMPLES_PER_FORMAT = 24

# 2019-01-01 and 2026-01-01 as unix seconds. Every generated timestamp lands in
# this window, well inside the 1990..2040 band the timestamp detector accepts.
UNIX_2019 = 1_546_300_800
UNIX_2026 = 1_767_225_600

WORDS = (
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
)


@dataclass(frozen=True, slots=True)
class TruthField:
    """One field the analysis is expected to recover."""

    offset: int
    size: int
    type_name: str
    role: str
    also_accept: tuple[str, ...] = ()

    @property
    def accepted(self) -> frozenset[str]:
        """Return every type name that counts as a correct recovery."""
        return frozenset({self.type_name, *self.also_accept})


@dataclass(frozen=True, slots=True)
class TruthRelation:
    """One relationship the analysis is expected to prove.

    A negative ``subject_offset`` counts backwards from the end of the file,
    which is how trailing checksums are addressed.
    """

    kind: RelationKind
    subject_offset: int
    role: str


@dataclass(frozen=True, slots=True)
class SyntheticFormat:
    """A generated format together with everything known to be true about it."""

    key: str
    name: str
    summary: str
    seed: int
    builder: Callable[[random.Random, int], bytes]
    fields: tuple[TruthField, ...]
    relations: tuple[TruthRelation, ...] = ()
    record_size: int = 0
    record_fields: tuple[TruthField, ...] = ()
    opaque: tuple[str, ...] = field(default_factory=tuple)
    # Whether a compressed span is present and must be refused rather than
    # explained. The check runs both ways: a format without one must not report
    # one either.
    expects_blob: bool = False


def _padded(data: bytes, size: int) -> bytes:
    if len(data) > size:
        raise ValueError(f"{len(data)} bytes do not fit in a {size}-byte field")
    return data.ljust(size, b"\x00")


FORMAT_A_MAGIC = b"ARCA"
FORMAT_A_VERSION = 3
FORMAT_A_SIZE = 32
FORMAT_A_HEADER = "<4sHHIfI"


def _build_a(rng: random.Random, index: int) -> bytes:
    header = struct.pack(
        FORMAT_A_HEADER,
        FORMAT_A_MAGIC,
        FORMAT_A_VERSION,
        rng.choice((0, 1, 2)),
        100_000 + index * 4099 + rng.randrange(512),
        rng.uniform(0.0, 1.0),
        rng.randrange(UNIX_2019, UNIX_2026),
    )
    return _padded(header, FORMAT_A_SIZE)


FORMAT_B_MAGIC = b"BLOB"
FORMAT_B_VERSION = 1
FORMAT_B_HEADER_SIZE = 16
FORMAT_B_TRAILER_SIZE = 4
# A 24-symbol alphabet above the ASCII range: about 4.6 bits per byte, so the
# payload is neither compressible enough to read as a blob nor printable enough
# to read as text. It has to stay genuinely unexplainable.
FORMAT_B_ALPHABET = bytes(range(0x80, 0x98))


def _build_b(rng: random.Random, _index: int) -> bytes:
    payload = bytes(rng.choice(FORMAT_B_ALPHABET) for _ in range(rng.randrange(96, 512)))
    body = (
        struct.pack(
            "<4sHHII",
            FORMAT_B_MAGIC,
            FORMAT_B_VERSION,
            0,
            len(payload),
            rng.randrange(1, 1 << 24),
        )
        + payload
    )
    return body + struct.pack("<I", zlib.crc32(body))


FORMAT_C_MAGIC = b"CTBL"
FORMAT_C_RECORD_MAGIC = b"RCD!"
FORMAT_C_HEADER_SIZE = 16
FORMAT_C_RECORD_SIZE = 16
FORMAT_C_FLAG_BITS = 3


def _build_c_record(rng: random.Random) -> bytes:
    return struct.pack(
        "<IBBHf4s",
        rng.randrange(1, 1 << 24),
        rng.choice((1, 2, 3)),
        rng.getrandbits(FORMAT_C_FLAG_BITS),
        rng.randrange(0, 1000),
        rng.uniform(0.0, 100.0),
        FORMAT_C_RECORD_MAGIC,
    )


def _build_c(rng: random.Random, _index: int) -> bytes:
    count = rng.randrange(4, 13)
    records = b"".join(_build_c_record(rng) for _ in range(count))
    header = struct.pack(
        "<4sIII", FORMAT_C_MAGIC, count, FORMAT_C_RECORD_SIZE, count * FORMAT_C_RECORD_SIZE
    )
    return header + records


FORMAT_D_MAGIC = b"DSTR"
FORMAT_D_VERSION = 2
FORMAT_D_SIZE = 128
FORMAT_D_NAME_SIZE = 16
FORMAT_D_TITLE_SIZE = 32
FORMAT_D_NOTES_OFFSET = 0x40


def _build_d(rng: random.Random, index: int) -> bytes:
    name = f"{rng.choice(WORDS)}-{rng.randrange(100, 1000)}".encode("ascii")
    title = f"{rng.choice(WORDS)} {rng.choice(WORDS)}"[: FORMAT_D_TITLE_SIZE // 2]
    first_note = f"{rng.choice(WORDS)}#{rng.randrange(10, 100)}".encode("ascii")
    second_note = f"{rng.choice(WORDS)}@{rng.randrange(10, 100)}".encode("ascii")
    notes = _padded(
        first_note + b"\x00" + second_note + b"\x00", FORMAT_D_SIZE - FORMAT_D_NOTES_OFFSET
    )
    header = struct.pack(
        "<4sHH16s32sII",
        FORMAT_D_MAGIC,
        FORMAT_D_VERSION,
        0,
        _padded(name, FORMAT_D_NAME_SIZE),
        _padded(title.encode("utf-16-le"), FORMAT_D_TITLE_SIZE),
        FORMAT_D_NOTES_OFFSET + len(first_note) + 1,
        index * 7919 + rng.randrange(1, 1 << 20),
    )
    return header + notes


FORMAT_E_MAGIC = b"EBLB"
FORMAT_E_VERSION = 1
FORMAT_E_METHOD = 8
FORMAT_E_HEADER_SIZE = 16
FORMAT_E_COMPRESSION_LEVEL = 9
# Half the payload is incompressible noise and half is runs, so deflate emits
# real Huffman blocks whose size is not an affine function of the input length.
FORMAT_E_RUN_FRACTION = 0.5


def _build_e(rng: random.Random, _index: int) -> bytes:
    # Deflating pure random bytes emits stored blocks, whose overhead is a fixed
    # eleven bytes and whose header repeats the uncompressed length verbatim.
    # Both are exact relations that a real compressed payload does not have, and
    # the tool would be right to report them. Mixing runs into the payload makes
    # deflate produce real Huffman blocks instead.
    chunks: list[bytes] = []
    target = rng.randrange(1024, 3072)
    while sum(len(chunk) for chunk in chunks) < target:
        length = rng.randrange(16, 128)
        chunks.append(
            rng.randbytes(length)
            if rng.random() < FORMAT_E_RUN_FRACTION
            else bytes([rng.randrange(256)]) * length
        )
    raw = b"".join(chunks)[:target]
    blob = zlib.compress(raw, FORMAT_E_COMPRESSION_LEVEL)
    header = struct.pack(
        "<4sHHII", FORMAT_E_MAGIC, FORMAT_E_VERSION, FORMAT_E_METHOD, len(raw), len(blob)
    )
    return header + blob


FORMAT_F_MAGIC = b"FTMS"
FORMAT_F_SIZE = 44


def _build_f(rng: random.Random, index: int) -> bytes:
    seconds = rng.randrange(UNIX_2019, UNIX_2026)
    header = struct.pack(
        "<4sIQQQI",
        FORMAT_F_MAGIC,
        seconds,
        seconds * 1000 + rng.randrange(1000),
        (seconds + FILETIME_EPOCH_OFFSET) * HUNDRED_NS_PER_SECOND
        + rng.randrange(HUNDRED_NS_PER_SECOND),
        (seconds + TICKS_EPOCH_OFFSET) * HUNDRED_NS_PER_SECOND
        + rng.randrange(HUNDRED_NS_PER_SECOND),
        index * 65_537 + rng.randrange(1, 4096),
    )
    return _padded(header, FORMAT_F_SIZE)


FORMAT_G_MAGIC = b"GNUM"
FORMAT_G_SIZE = 40


def _build_g(rng: random.Random, index: int) -> bytes:
    body = b"".join(
        (
            struct.pack("<4sh", FORMAT_G_MAGIC, rng.randrange(-1000, 1001)),
            struct.pack(">H", rng.randrange(0, 1001)),
            struct.pack("<i", rng.randrange(-2_000_000, 2_000_001)),
            struct.pack(">I", rng.randrange(0, 2_000_000_000)),
            struct.pack("<Qd", rng.randrange(1 << 40, 1 << 44), rng.uniform(-1000.0, 1000.0)),
            struct.pack(">f", rng.uniform(-100.0, 100.0)),
            struct.pack("<I", index * 65_537 + rng.randrange(1, 4096)),
        )
    )
    return _padded(body, FORMAT_G_SIZE)


FORMATS: tuple[SyntheticFormat, ...] = (
    SyntheticFormat(
        key="A",
        name="fixed-record",
        summary="fixed-size header: magic, version, flags, counter, float and a timestamp",
        seed=0xA10001,
        builder=_build_a,
        fields=(
            TruthField(0x00, 4, "magic[4]", "magic ARCA"),
            TruthField(0x04, 2, "const[2]", "format version 3"),
            TruthField(0x06, 2, "enum16le", "flags, three values", ("enum8", "bits8", "u8")),
            TruthField(0x08, 4, "u32le", "monotonic counter"),
            TruthField(0x0C, 4, "f32le", "ratio in 0..1"),
            TruthField(0x10, 4, "unix32le", "creation time"),
        ),
        opaque=("0x14..0x20 is zero padding and carries nothing",),
    ),
    SyntheticFormat(
        key="B",
        name="length-and-crc",
        summary="header with a u32 length prefix, variable payload and a trailing CRC32",
        seed=0xB20002,
        builder=_build_b,
        fields=(
            TruthField(0x00, 4, "magic[4]", "magic BLOB"),
            TruthField(0x04, 4, "const[4]", "version 1 followed by a reserved u16"),
            TruthField(0x08, 4, "u32le", "payload length", ("u16le",)),
            TruthField(0x0C, 4, "u32le", "sequence number"),
        ),
        relations=(
            TruthRelation(RelationKind.LENGTH, 0x08, "payload length plus 20 equals the file size"),
            TruthRelation(RelationKind.CHECKSUM, -4, "trailing CRC32 over everything before it"),
        ),
        opaque=("the payload between the header and the CRC is not structured",),
    ),
    SyntheticFormat(
        key="C",
        name="counted-records",
        summary="count field followed by an array of equal-size records",
        seed=0xC30003,
        builder=_build_c,
        fields=(
            TruthField(0x00, 4, "magic[4]", "magic CTBL"),
            TruthField(0x04, 4, "u32le", "record count", ("u8",)),
            TruthField(0x08, 4, "const[4]", "record size, always 16"),
            TruthField(0x0C, 4, "u32le", "payload bytes", ("u16le",)),
        ),
        relations=(
            TruthRelation(RelationKind.COUNT, 0x04, "count equals the number of 16-byte records"),
            TruthRelation(RelationKind.LENGTH, 0x0C, "payload bytes plus 16 equals the file size"),
        ),
        record_size=FORMAT_C_RECORD_SIZE,
        record_fields=(
            TruthField(0x00, 4, "u32le", "record id"),
            TruthField(0x04, 1, "enum8", "record kind, three values", ("u8",)),
            TruthField(0x05, 1, "bits8", "three flag bits", ("enum8", "u8")),
            TruthField(0x06, 2, "u16le", "weight in 0..999"),
            TruthField(0x08, 4, "f32le", "value in 0..100"),
            TruthField(0x0C, 4, "magic[4]", "record magic RCD!"),
        ),
    ),
    SyntheticFormat(
        key="D",
        name="strings",
        summary="fixed-width ASCII and UTF-16LE fields plus a pointer to a trailing note",
        seed=0xD40004,
        builder=_build_d,
        fields=(
            TruthField(0x00, 4, "magic[4]", "magic DSTR"),
            TruthField(0x04, 4, "const[4]", "version 2 followed by a reserved u16"),
            TruthField(0x08, 16, "ascii[16]", "name, null padded"),
            TruthField(0x18, 32, "utf16le[32]", "title, null padded"),
            TruthField(0x38, 4, "u32le", "offset of the second note", ("u8", "u16le")),
            TruthField(0x3C, 4, "u32le", "record id"),
        ),
        relations=(
            TruthRelation(RelationKind.OFFSET, 0x38, "points at a null-terminated ASCII string"),
        ),
        opaque=("0x40..0x80 holds two null-terminated notes and zero filler",),
    ),
    SyntheticFormat(
        key="E",
        name="opaque-blob",
        summary="plausible header followed by a deflate stream that cannot be explained",
        seed=0xE50005,
        builder=_build_e,
        fields=(
            TruthField(0x00, 4, "magic[4]", "magic EBLB"),
            TruthField(0x04, 4, "const[4]", "version 1 and method 8"),
            TruthField(0x08, 4, "u32le", "uncompressed size", ("u16le",)),
            TruthField(0x0C, 4, "u32le", "compressed size", ("u16le",)),
        ),
        relations=(
            TruthRelation(
                RelationKind.LENGTH, 0x0C, "compressed size plus 16 equals the file size"
            ),
        ),
        opaque=("0x10 to EOF is a deflate stream and must be reported as unexplained",),
        expects_blob=True,
    ),
    SyntheticFormat(
        key="F",
        name="timestamps",
        summary="the four timestamp encodings the tool knows, side by side",
        seed=0xF60006,
        builder=_build_f,
        fields=(
            TruthField(0x00, 4, "magic[4]", "magic FTMS"),
            TruthField(0x04, 4, "unix32le", "unix seconds"),
            TruthField(0x08, 8, "unixms64le", "unix milliseconds"),
            TruthField(0x10, 8, "filetime64le", "windows FILETIME"),
            TruthField(0x18, 8, "ticks64le", "dotnet DateTime ticks"),
            TruthField(0x20, 4, "u32le", "sequence number"),
        ),
        opaque=("0x24..0x2C is zero padding and carries nothing",),
    ),
    SyntheticFormat(
        key="G",
        name="numeric-zoo",
        summary="signed, big-endian and 64-bit numerics that no other format exercises",
        seed=0x67_0007,
        builder=_build_g,
        fields=(
            TruthField(0x00, 4, "magic[4]", "magic GNUM"),
            TruthField(0x04, 2, "i16le", "signed 16-bit, both signs present"),
            TruthField(0x06, 2, "u16be", "big-endian unsigned 16-bit"),
            TruthField(0x08, 4, "i32le", "signed 32-bit, both signs present"),
            TruthField(0x0C, 4, "u32be", "big-endian unsigned 32-bit"),
            TruthField(0x10, 8, "u64le", "unsigned 64-bit"),
            TruthField(0x18, 8, "f64le", "double in -1000..1000"),
            TruthField(0x20, 4, "f32be", "big-endian float in -100..100"),
            TruthField(0x24, 4, "u32le", "sequence number"),
        ),
    ),
)


def format_by_key(key: str) -> SyntheticFormat:
    """Return the format with the given single-letter key."""
    for candidate in FORMATS:
        if candidate.key == key:
            return candidate
    raise KeyError(f"no synthetic format with key {key!r}")


def generate(fmt: SyntheticFormat, count: int = SAMPLES_PER_FORMAT) -> tuple[bytes, ...]:
    """Generate ``count`` samples of ``fmt`` from its fixed seed."""
    rng = random.Random(fmt.seed)
    return tuple(fmt.builder(rng, index) for index in range(count))


def build_corpus(fmt: SyntheticFormat, count: int = SAMPLES_PER_FORMAT) -> Corpus:
    """Generate a corpus in memory, so that ``--self-test`` touches no filesystem."""
    blobs = generate(fmt, count)
    samples = tuple(Sample(f"{fmt.key}_{index:03d}.bin", data) for index, data in enumerate(blobs))
    return Corpus(samples=samples, discovered=len(samples))


@dataclass(frozen=True, slots=True)
class Scorecard:
    """How much of one format's declared ground truth the analysis recovered."""

    key: str
    name: str
    fields: tuple[int, int]
    relations: tuple[int, int]
    record_fields: tuple[int, int]
    opaque_ok: bool
    problems: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Return whether everything declared was recovered and nothing invented."""
        return not self.problems


def _score_fields(
    declared: Sequence[TruthField],
    found: Mapping[int, Field],
    label: str,
    problems: list[str],
) -> int:
    hits = 0
    for truth in declared:
        recovered = found.get(truth.offset)
        if recovered is None:
            problems.append(f"{label} {truth.offset:#06x} not found ({truth.role})")
        elif recovered.type_name not in truth.accepted:
            problems.append(f"{label} {truth.offset:#06x} read as {recovered.type_name}")
        elif recovered.size != truth.size:
            problems.append(f"{label} {truth.offset:#06x} sized {recovered.size}, not {truth.size}")
        else:
            hits += 1
    return hits


def score(fmt: SyntheticFormat) -> Scorecard:
    """Run the analysis on one synthetic format and grade it against its schema."""
    report = analyze(build_corpus(fmt))
    problems: list[str] = []

    fields = _score_fields(
        fmt.fields, {item.offset: item for item in report.fields}, "field", problems
    )

    proved = {(relation.kind, relation.subject_offset) for relation in report.relations}
    relations = 0
    for truth in fmt.relations:
        if (truth.kind, truth.subject_offset) in proved:
            relations += 1
        else:
            problems.append(f"relation {truth.kind.value} at {truth.subject_offset} not proved")

    record_fields = 0
    if fmt.record_fields:
        if report.records:
            record_fields = _score_fields(
                fmt.record_fields,
                {item.offset: item for item in report.records[0].fields},
                "record field",
                problems,
            )
        else:
            problems.append("no record array was segmented")

    blobs = [region for region in report.regions if region.kind is RegionKind.HIGH_ENTROPY]
    opaque_ok = bool(blobs) == fmt.expects_blob
    if not opaque_ok:
        problems.append(
            "a compressed region was reported where none exists"
            if blobs
            else "the compressed region was explained instead of refused"
        )

    return Scorecard(
        key=fmt.key,
        name=fmt.name,
        fields=(fields, len(fmt.fields)),
        relations=(relations, len(fmt.relations)),
        record_fields=(record_fields, len(fmt.record_fields)),
        opaque_ok=opaque_ok,
        problems=tuple(problems),
    )


def score_all() -> tuple[Scorecard, ...]:
    """Grade every synthetic format."""
    return tuple(score(fmt) for fmt in FORMATS)
