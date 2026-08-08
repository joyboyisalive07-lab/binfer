<img src="docs/img/logo.svg" alt="binfer" width="420">

[![CI](https://github.com/joyboyisalive07-lab/binfer/actions/workflows/ci.yml/badge.svg)](https://github.com/joyboyisalive07-lab/binfer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

English | [Русский](README.ru.md)

binfer reads a directory of sample files in the same unknown binary format and
prints a text report describing the fields it found, with the evidence for each
one.

## The problem

You have thirty save files from a game, or thirty exports from a program whose
format nobody documented. Opening one in a hex editor tells you almost nothing:
every byte could be anything. Opening thirty and comparing them tells you a
great deal, because the bytes that never change are structure and the bytes that
change together are a field. Doing that comparison by hand is tedious and
error-prone, and it is exactly what a program should do.

binfer does the comparison and then, crucially, refuses to guess. Four uniformly
random bytes decode perfectly well as a 32-bit integer; nothing in the bytes says
otherwise. So a reading is reported only when the corpus carries positive
evidence for it, and every claim arrives with the count that supports it.

## A real report

This is the actual output of `binfer samples/`, on a corpus of twenty-four files
in a format with a length prefix and a trailing CRC32:

```
CORPUS
  24 files analysed
  145..522 bytes, mean 314, median 322
  sizes are not uniform, 24 distinct sizes

LAYOUT
  OFFSET    SIZE  TYPE             VALUE/RANGE       CONFIDENCE EVIDENCE
  EOF-4        4  u32le            see checksum      proved     matches in 24/24
  0x0000       4  magic[4]         'BLOB'            high       identical in 24/24
  0x0004       4  const[4]         01 00 00 00       high       identical in 24/24
  0x0008       4  u32le / u16le    125..502          high       top 2 byte(s) zero in 24/24
  0x000C       4  u32le            547680..16515876  high       top 1 byte(s) zero in 24/24

RELATIONS
  EOF-4 u32le    checksum crc32 over the bytes before it       proved     matches in 24/24
  0x0008 u32le   length   value + 20 == size                   proved     holds exactly in 24/24

REGIONS
  0x0010..EOF-4      unexplained   4.21 bits/byte  nothing found accounts for these bytes

NOTES
  - sizes differ, so offsets were compared over a 72-byte head window and a 72-byte tail window; the
    span between them has no common offset and was measured but not aligned
```

The payload between the header and the checksum is not structure binfer failed
to find. It is a variable-length blob with no structure to find, and the report
says so rather than filling the table with plausible-looking rows.

## Install

Download `binfer.exe` from the
[latest release](https://github.com/joyboyisalive07-lab/binfer/releases/latest).
It is a single file with no installer and no runtime dependencies, and it runs
from any folder including a flash drive.

Or from source, which needs only Python 3.12 or newer:

```bash
git clone https://github.com/joyboyisalive07-lab/binfer
cd binfer
python -m pip install -e .
```

### A note on Windows Defender and SmartScreen

Executables built with PyInstaller are sometimes flagged by Windows Defender or
warned about by SmartScreen. This is not specific to binfer: a PyInstaller
one-file executable unpacks a Python interpreter into a temporary directory and
runs it, which is a shape that some heuristics treat as suspicious, and the
binary is unsigned because code-signing certificates cost money this project
does not have. If that is a problem for you, build from source with
`tools/build.ps1` or run `python -m binfer`; both do exactly the same work.

## Usage

```
binfer <directory>                 analyse a corpus and print the report
binfer <directory> --json FILE     also write machine-readable findings
binfer <directory> --ksy FILE      also write a Kaitai Struct draft
binfer --self-test                 grade the tool against known ground truth
binfer --version
binfer --help
```

Flags: `--min-confidence {low,high,proved}`, `--max-files N`, `--record-size N`,
`--no-color`.

Exit codes are 0 for success, 1 when the analysis could not run or the self test
found a regression, and 2 for a usage error.

Point it at a directory of at least four files; twelve or more makes the
statistics worth trusting, and the report says so when there are fewer.

```bash
binfer ./saves --json findings.json --ksy saves.ksy
```

`--self-test` needs no files of your own. It builds seven synthetic formats in
memory, each with a schema written down before the bytes existed, runs the full
analysis over them and grades the result:

<img src="docs/img/report.svg" alt="binfer --self-test output" width="560">

## How the analysis works

<img src="docs/img/pipeline.svg" alt="the six analysis stages" width="900">

Each stage may decline to conclude anything. What no stage explains is reported
as unexplained, and that is a result, not a failure.

## Confidence tiers

There are exactly three, and they mean specific things.

| Tier | Meaning |
| --- | --- |
| `proved` | Holds in 100% of samples and is falsifiable. Checksums and length arithmetic: a single mismatching sample would kill the finding outright. |
| `high` | Holds in 100% of samples but rests on a statistical argument. Type guesses live here: `u32le` is the best reading of those four bytes, not a provable fact about them. |
| `low` | Holds in most samples but not all, reported so you can judge it. A string field that decodes cleanly in 20 files out of 22 is worth seeing. |

`--min-confidence` hides the tiers below the one you ask for, in the field table
and in the nested record table alike.

## What v1.0 deliberately does not do

- **It does not guess a record size.** Segmentation follows a count field whose
  arithmetic was proved exactly, or the `--record-size` you supply. A blind
  search over plausible strides is a real technique, but none of the synthetic
  formats here would demonstrate that it works, so it is not shipped.
- **It does not decompress anything.** A compressed or encrypted span is
  reported as high-entropy and unexplained. That is the answer, not a gap to be
  filled later.
- **It does not follow pointers into nested structures.** A pointer that lands
  on a string is reported as a pointer; what the string means is your problem.
- **It does not parse a single file.** Everything rests on comparing samples, so
  a corpus of one tells it nothing and it will say so.
- **It never writes anywhere you did not name.** No cache, no temporary corpus,
  no config file. `--self-test` builds its samples in memory.

## Building from source

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python tools/coverage.py
pwsh tools/build.ps1
```

`tools/build.ps1` produces `dist/binfer.exe` and refuses to call the build done
until that executable has run `--self-test` and recovered the ground truth of
every synthetic format. A binary that starts and prints a version number has
proved nothing.

`tools/gen_corpus.py` writes the synthetic corpora to disk if you want to open
them in a hex editor. `tools/make_images.py` regenerates the images above; the
self-test picture is typeset from output the tool actually produced.

## License

MIT, see [LICENSE](LICENSE).
