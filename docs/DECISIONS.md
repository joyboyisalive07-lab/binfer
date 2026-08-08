# Decisions

Every non-obvious choice, one line each, with the reason. Newest phase last.

## Phase 0 - scaffold

- Package layout is `src/`-based: keeps an accidental import of the working tree
  from shadowing the installed package during tests.
- `binfer/types.py` and `binfer/stats.py` shadow stdlib module names; Python 3
  absolute imports make this harmless inside a package, so the ruff rule `A005`
  is disabled rather than the modules renamed.
- Confidence is an `IntEnum`, not a `str` enum: overlap resolution needs to pick
  the stronger of two findings, and a plain `<` is clearer than a rank table.
- Overlap resolution is greedy interval selection sorted by
  (tier, width, offset, type name) - a total order, so the output cannot depend
  on which stage emitted a finding first.
- Every public container is a `tuple`, never a `set` or `dict` view: the report
  must be byte-identical across runs and leaked iteration order is the usual way
  that breaks.
- `requires-python = ">=3.12"` even though development targets 3.14, because the
  CI matrix is required to cover 3.12.
- Ruff runs `select = ["ALL"]`; the ignore list is short and each entry carries
  its reason in `pyproject.toml`. `PLR2004` stays enabled on purpose - it forces
  every threshold in the heuristics to become a named constant.
- Line coverage will be measured with the stdlib `trace` module rather than
  `pytest-cov`, because the dev dependency list is fixed at pytest, ruff and
  pyinstaller.
- No empty placeholder modules are committed: a module appears in the commit of
  the phase that fills it, so the tree never contains dead files.
- MIT copyright year is 2026, the year of first publication.
- `CPY001` is disabled: the licence text belongs in `LICENSE`, and a header
  repeated in every module is noise that has to be maintained.

## Phase 1 - corpus and column statistics

- The sample directory is not traversed recursively: a corpus is a flat set of
  files of one format, and pulling in a nested directory silently mixes formats.
- Files are taken in name order, so `--max-files N` selects the same subset on
  every run and on every platform.
- Empty files are skipped with a warning rather than aborting: one truncated
  download should not block analysis of the other thirty samples.
- Files above 64 MiB are skipped with a warning. Every stage is linear in file
  size, and a sample far larger than the rest is nearly always a different thing
  that happens to share the directory.
- Head and tail windows are capped at 64 KiB each. Whatever falls between them
  is reported as unanalysed, never silently dropped.
- Window budget for varying sizes is `min_size // 2` per side, so the two
  windows can never overlap even on the shortest sample.
- A uniform corpus larger than two window budgets keeps `FIXED` mode: offsets
  stay absolute, only the coverage shrinks.
- `median_size` uses `median_low`, so the reported median is always a size an
  actual sample has, not an average of two.
- Column entropy is normalised against `log2(sample count)`, because a 12-file
  corpus can never show more than 3.58 bits at one offset and an absolute
  threshold would classify nothing.
- Entropy terms are summed in sorted order: float addition is not associative
  and the report has to be byte-identical between runs.
- Two separate entropy measures exist and must not be confused - across the
  corpus at one offset (how much a field varies), and inside one span of one
  file (whether it looks compressed). A counter maximises the first and
  minimises the second.
- A ragged window raises instead of truncating: unequal rows mean the caller
  aligned the corpus wrongly, and silence would hide it.

## Phase 2 - synthetic corpora

- The generator lives in `src/binfer/synth.py`, not in `tools/gen_corpus.py` as
  the original layout sketched. `binfer --self-test` has to work from a pip
  install and from the frozen executable, and neither ships `tools/`.
  `tools/gen_corpus.py` stays as a thin writer for inspecting samples by hand.
- `--self-test` builds its corpora in memory. The tool must never write outside
  paths the user passed, and a self test that needs a scratch directory would.
- Six formats, not five: the four timestamp encodings need ground truth of their
  own, and hiding them inside another format would make a failure ambiguous.
- Ground truth may list alternative acceptable type names. A two-byte value
  followed by two constant zero bytes genuinely reads either way, and a test
  that demands one answer would be testing a coin flip, not the tool.
- Each format carries its own fixed seed constant rather than deriving one from
  its key: `hash()` is salted per process and would break reproducibility.
- Format D is fixed-size on purpose. With variable sizes the head window would
  stop inside the UTF-16 field and the pointer at 0x38 would fall outside every
  comparable window, so the pointer relation could never be proved.
- Format E compresses random bytes: deflate of random input is a genuine
  deflate stream and stays incompressible, which is what the region stage has to
  refuse to explain. Its `78 DA` header is legitimately constant and the tool
  reporting those two bytes is correct, not a false positive.
- Format B's payload uses a 24-symbol alphabet above the ASCII range, giving
  about 4.6 bits per byte: neither compressible enough to read as a blob nor
  printable enough to read as text, so it stays genuinely unexplainable.

## Phase 3 - field typing

- A seventh format was added, the numeric zoo. Signed, big-endian and 64-bit
  readings are all required by the specification and nothing else in the corpus
  exercised them, so by the project's own rule they would have had to be
  deleted instead.
- Format C's record gained a real three-bit flag byte in place of a zero pad,
  because the bitfield detector otherwise had no ground truth either.
- An integer reading is emitted only with positive evidence: constant zero high
  bytes, a high byte that never approaches 0xFF, byte entropy falling towards
  the significant end, or a high byte confined to the neighbourhoods of 0x00 and
  0xFF. Four uniformly random bytes decode as a u32 just fine, so without this
  the field table fills with invented integers wherever the data is opaque.
- The support floor is 0.45, which corresponds to a high byte never exceeding
  0x8C. Twenty-four uniformly random samples clear that about twice in 10^7.
- Constant printable runs of four bytes or more are hard anchors that nothing
  may straddle. Without them a spurious big-endian timestamp landing across a
  magic shifted every field in the file.
- Constant all-zero runs of four bytes or more are padding anchors for numerics
  only, and an integer may still claim the first byte as its high byte. Strings
  are exempt because the NUL tail of a fixed-width string is the same pattern.
- Multi-byte fields are assumed to be aligned to their own width, worth 0.15 of
  the score. Readings that start mid-field are the commonest false positive of a
  dense offset scan, and this is the cheapest signal that separates them.
- Scores within 0.05 count as a tie, broken towards the hypothesis covering more
  bytes: a rule explaining thirty-two bytes is a better account than one
  explaining two. This is what lets a UTF-16 field beat the u16 sitting on its
  first two bytes.
- A timestamp outranks every other reading, because all samples landing in one
  fifty-year window is the most specific claim the stage can make. It is
  suppressed on spans that are entirely printable-or-NUL, since four lowercase
  letters decode to 2021-2035, and on spans carrying two or more constant
  non-zero bytes, since a zlib header reads as a plausible clock in every sample.
- Where a span reads as both a float and a timestamp, the float wins: a float32
  holding 0..100 also decodes as a unix32 in 2002-2005, and the float test
  (finite, narrow exponent band, plausible magnitude) is the more constrained.
- Floats must fall between 1e-9 and 1e15 in magnitude. Bytes belonging to some
  other field routinely decode as 1e-305 or 1e+274, and nothing a program stores
  in a file looks like that.
- An enum value seen exactly once is an outlier, not a member of a closed set.
  Without that rule a corpus holding two odd files turns every column into an
  enum of "the usual value plus whatever those files carry".
- Bitfields need at least three varying bits with every combination observed.
  Two bits say nothing a four-valued enum does not already say, and the high
  byte of a big-endian u16 looks exactly like a two-bit field.
- Booleans are not a separate type. A two-valued enum prints its value set, so
  `enum8 {0, 1}` already says everything `bool8` would, and one fewer heuristic
  needs ground truth.
- All typing findings hold in 100% of samples and are therefore `high` by the
  README's definition. Only strings can be partial, at 80% or more, and those
  are the sole source of `low` in this stage.
- Known gap for the region stage: in format E the first few bytes of the deflate
  stream are genuinely structured - the `78 DA` header is constant and the block
  header bytes are low-variance - so the typing stage reports them. Suppressing
  findings inside a high-entropy region belongs to the region stage, not here.

## Phase 4 - relations

- The relation scan enumerates spans directly instead of reading the typing
  stage's fields. A CRC-32 field is four uniform bytes, which the typing stage
  deliberately refuses to name, so relying on named fields would lose exactly
  the case this stage exists for.
- A count is a length relation whose stride is greater than one: the field
  counts units of `k` bytes. That definition is self-contained, and the fitted
  `k` is handed to the record stage as the record size, so nothing has to guess
  it twice.
- Alignment outranks width when two fitting readings overlap. A pointer at 0x38
  also reads as an eight-byte big-endian value starting at 0x31 whenever the
  seven bytes in front of it are zero, and that reading passes every test the
  real field does. Only its alignment gives it away.
- Overlapping fits are deduplicated, not merely nested ones. In format C a
  misaligned `u32be` straddling two fields fitted the same file-size relation as
  the real length field beside it.
- A uniform corpus is scanned from the start only. Absolute offsets already
  reach the end of the file, and scanning from both ends reported the same
  field twice under two different names.
- Pointers are `high`, not `proved`. A value landing on a string in every sample
  is strong, but it is not arithmetic; the README reserves `proved` for
  checksums and length math.
- Constant spans are skipped everywhere. A constant "checksum" would match only
  because nothing ever moved, and a constant value cannot pin down `k`.
- The checksum search tests one sample first and only then the other twenty
  three. A hopeless span costs one pass instead of twenty four.
- Checksum spans are limited to 64 bytes from either end, trailers first, and
  the whole search has a hashing budget in bytes. When the budget truncates the
  search the report says how many spans were covered, rather than silently
  doing less.
- CRC-16 is implemented here with generated tables and validated against the
  published check values for ARC, MODBUS, CCITT-FALSE and XMODEM. Being pure
  Python it is skipped above 256 KiB per sample, and the report says so; zlib
  still covers CRC-32 and Adler-32 at any size.
- Format E now compresses a payload of mixed noise and runs. Deflating pure
  random bytes emits stored blocks, whose overhead is a fixed eleven bytes and
  whose header repeats the uncompressed length verbatim - two exact relations
  that the tool correctly found and that no real compressed payload has.

## Phase 5 - repeated records

- The record size is never guessed. It comes from a count field whose
  arithmetic the relation stage proved exactly, or from `--record-size`. A blind
  search over plausible strides is a real technique, but there is no synthetic
  format that would prove it works, and this project deletes heuristics with no
  ground truth rather than shipping them. The README says so under what v1.0
  does not do.
- Where the records start is inferred, because the arithmetic cannot say it. A
  count relation reads `value * k + c == file size` and does not reveal whether
  the `c` bytes sit in front of the records, behind them, or both. Every start
  that divides the region exactly is tried, and the one whose pooled records
  have the lowest mean column entropy wins: at the true start every field lands
  in the same column of every record, and at any other the fields smear.
- The start search stops at 256 bytes. Records begin after a header and headers
  are small; searching further would turn the start search into the blind stride
  search that was just refused, by the back door.
- A segmentation explaining no field is not reported at all. A record table full
  of unknowns is exactly the padding the honesty rule forbids.
- Records from every sample are pooled into one set of rows before typing. In
  format C the three-bit flag byte cannot be proved from a single window of
  24 samples, because all eight combinations rarely appear; pooled across
  roughly 190 records they always do, and the field is correctly reported as
  `bits8` rather than `u8`.
- The recursion runs the relation stage inside the record body too, so a
  checksum covering the record's own bytes is found. Length relations cannot
  fire there, since every record has the same size and the target never moves.

## Phase 6 - assembly and rendering

- Assembly lives in `src/binfer/analyze.py`, a second deviation from the
  original layout. `report.py` renders and must not conclude; `cli.py` must not
  carry analysis; the pipeline has to live somewhere and this is it.
- Offsets carry an anchor by sign: non-negative counts from the start, negative
  counts back from the end, and an end of zero means the end of the file. A
  corpus of varying sizes has no single absolute offset for its trailer, and
  printing one would be a wrong number rather than a missing one.
- Every proved relation subject gets a row in the field table. A CRC-32 field is
  four uniform bytes that the typing stage refuses to name, so without this the
  strongest finding in the file would appear only under RELATIONS and the layout
  would show a hole where it sits.
- High-entropy spans absorb the short findings pressed against them. The first
  bytes of a deflate stream are genuinely structured - the `78 DA` header never
  moves and the block header is low-variance - so the typing stage reports them
  correctly and the region stage has to overrule it. The walk stops at a magic
  run, at any field a relation proved, and at anything wider than eight bytes.
  This closes the gap left open in phase 3.
- When the whole trailer window measures as compressed, every field found inside
  it is dropped rather than absorbed one at a time. Anything typed inside a
  deflate stream is a regularity of the compressor, not a field of the format.
- Entropy is now compared against the most a span of that length could show,
  not against a fixed 7.2 bits. The plug-in estimator is biased downwards on
  short spans - 256 uniformly random bytes measure about 7.5 bits, not 8 - and
  the fixed threshold made a 232-byte deflate span look like structure.
- A record array runs to the end of the file, so when one is found the head and
  tail windows stop contributing fields beyond its start. Otherwise the last two
  records appeared a second time as EOF-anchored fields.
- The runner-up is printed in the type column as `winner / runner-up`, because
  the evidence count is the one thing the report may never drop and it has to
  fit on the same line.
- The Kaitai export is a draft and says so. Constant fields become `contents`
  assertions that actually validate a file, holes become explicit `unknown_*`
  attributes, a record array becomes a repeated type, and a trailer anchored to
  the end of the file becomes an open-ended span with a note, since Kaitai
  cannot express it without a size expression the corpus does not supply.

## Phase 7 - command line and self test

- `--min-confidence` takes `low`, `high` and `proved`, not the `medium` the
  original surface named. There is no medium tier; offering one would name a
  thing the README does not define.
- Exit codes are 0 for success, 1 for an analysis that could not run or a self
  test that found a regression, and 2 for a usage error, which is argparse's
  own convention.
- `--self-test` grades each format mechanically and prints the tally. Whether a
  compressed region is expected became a declared boolean on the format rather
  than prose, so the check runs both ways: a format without a blob must not
  report one either.
- Colour is off unless stdout is a terminal, and `NO_COLOR` in the environment
  turns it off regardless, following the usual convention.
- Coverage is measured by `tools/coverage.py`, which drives the stdlib `trace`
  module because the dev dependency list is fixed at pytest, ruff and
  pyinstaller. It uses `trace._find_executable_linenos`, a private helper, on
  the grounds that reimplementing it would add a second thing to be wrong.
- `--min-confidence` filters the nested record table as well as the top level.
  Filtering only the top level left hidden-tier rows visible inside the record
  layout, which the CLI tests caught.

## Phase 8 - the executable

- PyInstaller is pointed at `src/binfer/__main__.py`, the same module
  `python -m binfer` runs, so the frozen program and the source program take the
  same path into the CLI and cannot drift apart.
- `tools/build.ps1` refuses to call a build done until the executable has run
  `--self-test` and recovered the ground truth of every synthetic format, and
  until the version it reports matches the version in the source. A binary that
  starts and prints a version number has proved nothing.
- The bundle is stdlib only: 312 entries, 9.5 MB, with no third-party package
  in it. PyInstaller's analysis prints a banner from an unrelated package
  installed in this environment; nothing of it is bundled, and the build script
  does not care what else is installed.
- The spec file is generated rather than committed. It is derived entirely from
  the command line above, and a checked-in copy would be a second place for the
  build to be configured.

## Phase 9 - documentation, images and workflows

- `docs/img/report.svg` is typeset from the text `--self-test` actually printed,
  so the picture in the README cannot show output the program does not produce.
  CI regenerates all three images and fails if the committed files differ.
- The logo is a byte grid: filled columns are identical across the corpus,
  outlined ones vary. That is the tool's whole idea drawn with rectangles, and
  it needs no clip art and no gradient.
- All three images use one neutral grey rather than a light and a dark variant,
  so they stay legible on either background without theme switching.
- The report stays in English even in the Russian README, and the Russian README
  says why: a report is meant to be pasted into a forum post or an issue where
  the readers do not share a language.
- The release workflow publishes with `gh release create` rather than a
  third-party action. `gh` is on the runner, it authenticates with the default
  token, and a project whose selling point is having no dependencies should not
  acquire one in its release path.
- CI runs the self test as a separate step from pytest. The suite already covers
  it, but a release is cut from the executable and the same command has to be
  seen passing on both platforms.
