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
