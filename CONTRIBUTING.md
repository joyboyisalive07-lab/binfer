# Contributing

## Running the tests

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m ruff format --check .
python tools/coverage.py
```

`tools/coverage.py` drives the stdlib `trace` module and fails below 85% on the
core analysis modules.

## The standard library rule

The package has no runtime dependencies and will not acquire any. `zlib` covers
CRC-32 and Adler-32; everything else is written here. A pull request that adds a
runtime dependency will be declined regardless of how convenient the library is,
because the whole point of the executable is that it is one file with nothing
behind it.

Development dependencies are pytest, ruff and pyinstaller, and that list is also
closed.

## New heuristics need ground truth

This is the rule the project is built on. Any new detector, threshold or scoring
rule must come with a synthetic format in `src/binfer/synth.py` whose schema is
declared before the bytes exist, and a test proving the detector recovers it.

A binary-format guesser can look convincing and be wrong, and the output alone
does not distinguish the two. If a heuristic cannot be demonstrated against a
format whose layout is known in advance, it gets deleted rather than shipped.
Several already have been.

Two checks come free with that and are worth keeping in mind:

- `--self-test` must stay at 7 of 7, and the OPAQUE column runs both ways: a
  format with no compressed region must not report one either.
- A corpus of random files must produce nothing but size statistics. If a change
  makes the tool claim a field there, the change is wrong.

## Style

- Full type hints.
- Comments explain why a threshold was chosen or what an algorithm is called,
  never what the line below does.
- Every non-obvious decision goes in `docs/DECISIONS.md`, one line, with the
  reason.
- The report may never print a claim without the count that supports it.

## Sample files

Never commit sample files. `samples/` is in `.gitignore` for a reason: save
games and exports routinely embed usernames, machine names and account
identifiers, and a corpus large enough to analyse is large enough to leak them.
Use `python tools/gen_corpus.py <dir>` for something to look at.
