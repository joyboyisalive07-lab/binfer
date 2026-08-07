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
