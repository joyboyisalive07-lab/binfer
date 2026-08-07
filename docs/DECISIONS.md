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
