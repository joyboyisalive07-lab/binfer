"""End-to-end tests: the whole pipeline against the declared ground truth."""

from __future__ import annotations

import random

import pytest

from binfer.analyze import Options, analyze
from binfer.corpus import Corpus, Sample
from binfer.model import Confidence, RegionKind
from binfer.synth import FORMATS, build_corpus, format_by_key
from binfer.types import TYPING_SCAN_LIMIT


def report_for(key: str, **kwargs: object) -> object:
    return analyze(build_corpus(format_by_key(key)), Options(**kwargs))  # type: ignore[arg-type]


def random_corpus(count: int = 16, size: int = 96, seed: int = 7) -> Corpus:
    rng = random.Random(seed)
    samples = tuple(Sample(f"r{index:03d}.bin", rng.randbytes(size)) for index in range(count))
    return Corpus(samples=samples, discovered=count)


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_every_declared_field_survives_to_the_report(fmt) -> None:  # noqa: ANN001
    report = analyze(build_corpus(fmt))
    found = {field.offset: field for field in report.fields}
    for truth in fmt.fields:
        field = found.get(truth.offset)
        assert field is not None, f"{fmt.key}: {truth.offset:#x} vanished ({truth.role})"
        assert field.type_name in truth.accepted


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.key)
def test_every_declared_relation_survives_to_the_report(fmt) -> None:  # noqa: ANN001
    report = analyze(build_corpus(fmt))
    found = {(relation.kind, relation.subject_offset) for relation in report.relations}
    for truth in fmt.relations:
        assert (truth.kind, truth.subject_offset) in found, f"{fmt.key}: {truth.role}"


def test_the_record_array_replaces_the_fields_the_windows_saw_inside_it() -> None:
    report = analyze(build_corpus(format_by_key("C")))
    layout = report.records[0]
    assert all(field.end <= layout.start for field in report.fields)
    assert layout.fields


def test_a_compressed_blob_is_named_unexplained_and_holds_no_fields() -> None:
    report = analyze(build_corpus(format_by_key("E")))
    blobs = [region for region in report.regions if region.kind is RegionKind.HIGH_ENTROPY]
    assert len(blobs) == 1
    assert (blobs[0].start, blobs[0].end) == (0x10, 0)
    assert all(field.end <= 0x10 for field in report.fields)
    assert blobs[0].entropy > 7.0


def test_zero_padding_is_named_padding_rather_than_invented_fields() -> None:
    report = analyze(build_corpus(format_by_key("A")))
    padding = [region for region in report.regions if region.kind is RegionKind.PADDING]
    assert [(region.start, region.end) for region in padding] == [(0x14, 0)]
    assert all(field.end <= 0x14 for field in report.fields)


def test_a_fully_explained_format_leaves_no_region() -> None:
    assert analyze(build_corpus(format_by_key("C"))).regions == ()


def test_random_files_produce_nothing_but_size_statistics() -> None:
    report = analyze(random_corpus())
    assert report.fields == ()
    assert report.relations == ()
    assert report.records == ()
    assert [region.kind for region in report.regions] == [RegionKind.UNEXPLAINED]
    assert (report.regions[0].start, report.regions[0].end) == (0, 0)
    assert report.corpus.analyzed == 16


def test_a_checksum_the_typing_stage_could_not_name_still_gets_a_row() -> None:
    report = analyze(build_corpus(format_by_key("B")))
    checksum = next(field for field in report.fields if field.offset == -4)
    assert checksum.confidence is Confidence.PROVED
    assert checksum.evidence.claim == "matches"


def test_min_confidence_hides_weaker_findings_and_says_so() -> None:
    everything = analyze(build_corpus(format_by_key("D")))
    proved_only = report_for("D", min_confidence=Confidence.PROVED)
    assert len(proved_only.fields) < len(everything.fields)
    assert any("below the requested confidence" in note for note in proved_only.notes)


def test_an_explicit_record_size_is_honoured() -> None:
    report = report_for("C", record_size=16)
    assert report.records[0].origin == "--record-size 16"


def test_the_head_and_tail_split_is_explained_in_the_notes() -> None:
    report = analyze(build_corpus(format_by_key("B")))
    assert any("head window" in note for note in report.notes)


def test_regions_and_fields_never_overlap() -> None:
    for fmt in FORMATS:
        report = analyze(build_corpus(fmt))
        for region in report.regions:
            for field in report.fields:
                if (field.offset < 0) != (region.start < 0):
                    continue
                end = region.end if region.end != 0 else field.end
                assert not (field.offset < end and region.start < field.end), (
                    f"{fmt.key}: field at {field.offset:#x} sits inside a region"
                )


def test_a_window_wider_than_the_scan_limit_says_the_scan_was_capped() -> None:
    size = TYPING_SCAN_LIMIT + 512
    samples = tuple(
        Sample(f"big{index}.bin", b"WIDE" + bytes([index]) + bytes(size - 5)) for index in range(4)
    )
    report = analyze(Corpus(samples=samples, discovered=4))
    assert any("numeric scan covered the first" in note for note in report.notes)


def test_padding_is_reported_without_an_entropy_figure() -> None:
    report = analyze(build_corpus(format_by_key("F")))
    padding = next(region for region in report.regions if region.kind is RegionKind.PADDING)
    assert padding.entropy == 0.0
    assert padding.note == "zero filled in every sample"


def test_the_whole_analysis_is_reproducible() -> None:
    for fmt in FORMATS:
        assert analyze(build_corpus(fmt)) == analyze(build_corpus(fmt))
