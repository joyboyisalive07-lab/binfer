"""Generate the three SVG images the README embeds.

Nothing here is a mock-up. ``report.svg`` is produced by running the tool's own
self test and typesetting the text it actually printed, so the picture in the
README cannot claim output the program does not produce.

All three are monochrome, use no gradients and no raster assets, and are legible
on a light or a dark page.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from binfer.report import render_scorecard  # noqa: E402
from binfer.synth import score_all  # noqa: E402

OUT = ROOT / "docs" / "img"

# A neutral grey that stays legible on white and on a dark page, so the images
# need no theme switching.
INK = "#6e7681"
STRONG = "#30363d"
TERMINAL_BG = "#0d1117"
TERMINAL_FG = "#c9d1d9"
TERMINAL_DIM = "#8b949e"

MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
SANS = "ui-sans-serif, system-ui, Segoe UI, Helvetica, Arial, sans-serif"

FONT_SIZE = 13
CHAR_WIDTH = FONT_SIZE * 0.6
LINE_HEIGHT = 18
PADDING = 18


def _svg(width: float, height: float, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="{escape(title)}">\n'
        f"<title>{escape(title)}</title>\n{body}</svg>\n"
    )


def logo() -> str:
    """Draw a byte grid: filled columns are identical across the corpus, outlined ones vary.

    That is the whole idea of the tool in one mark, and it draws with rectangles
    and a wordmark only.
    """
    cell, gap, rows, columns = 9, 3, 5, 8
    # Columns 0-1 hold a magic, 2 is a small enum, the rest vary.
    filled = {0, 1}
    partial = {2}

    squares = []
    for column in range(columns):
        for row in range(rows):
            x = 20 + column * (cell + gap)
            y = 20 + row * (cell + gap)
            if column in filled or (column in partial and row % 2 == 0):
                squares.append(
                    f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="{STRONG}"/>'
                )
            else:
                squares.append(
                    f'<rect x="{x + 0.5}" y="{y + 0.5}" width="{cell - 1}" height="{cell - 1}" '
                    f'fill="none" stroke="{INK}" stroke-width="1"/>'
                )

    grid_width = columns * (cell + gap) - gap
    text_x = 20 + grid_width + 22
    body = (
        "\n".join(squares)
        + f'\n<text x="{text_x}" y="{20 + rows * (cell + gap) - gap - 12}" font-family="{MONO}" '
        f'font-size="34" fill="{STRONG}" letter-spacing="-0.5">binfer</text>\n'
        f'<text x="{text_x + 2}" y="{20 + rows * (cell + gap) - gap + 8}" font-family="{SANS}" '
        f'font-size="11" fill="{INK}">structure inference for unknown binaries</text>\n'
    )
    return _svg(text_x + 268, 20 + rows * (cell + gap) - gap + 22, body, "binfer")


STAGES = (
    ("1  CORPUS", ("load the samples,", "refuse fewer than four,", "align head and tail")),
    ("2  COLUMNS", ("per offset: histogram,", "entropy, and which bits", "ever move")),
    ("3  TYPES", ("score readings, refuse", "the ones nothing in the", "corpus supports")),
    ("4  RELATIONS", ("exact length, count,", "pointer and checksum;", "no approximations")),
    ("5  RECORDS", ("segment the counted", "region and run stages", "2-4 inside a record")),
    ("6  REPORT", ("resolve overlaps and", "name every span left", "unexplained")),
)


def pipeline() -> str:
    """Draw the six stages left to right, with the arrows between them."""
    box_width, box_height, gap = 145, 92, 16
    top, left = 26, 20

    parts = []
    for index, (title, lines) in enumerate(STAGES):
        x = left + index * (box_width + gap)
        parts.append(
            f'<rect x="{x}" y="{top}" width="{box_width}" height="{box_height}" rx="4" '
            f'fill="none" stroke="{INK}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{top + 22}" font-family="{MONO}" font-size="12" '
            f'fill="{STRONG}">{escape(title)}</text>'
        )
        for line_index, line in enumerate(lines):
            parts.append(
                f'<text x="{x + 12}" y="{top + 42 + line_index * 14}" font-family="{SANS}" '
                f'font-size="9.5" fill="{INK}">{escape(line)}</text>'
            )
        if index + 1 < len(STAGES):
            arrow_x = x + box_width
            middle = top + box_height / 2
            parts.append(
                f'<path d="M{arrow_x + 3} {middle} H{arrow_x + gap - 6}" stroke="{INK}" '
                f'stroke-width="1"/>'
            )
            parts.append(
                f'<path d="M{arrow_x + gap - 6} {middle - 3.5} L{arrow_x + gap - 1} {middle} '
                f'L{arrow_x + gap - 6} {middle + 3.5} Z" fill="{INK}"/>'
            )

    width = left * 2 + len(STAGES) * box_width + (len(STAGES) - 1) * gap
    caption = (
        f'<text x="{left}" y="{top + box_height + 22}" font-family="{SANS}" font-size="10" '
        f'fill="{INK}">Every stage may refuse to conclude. What no stage explains is '
        f"reported as unexplained, not filled in.</text>"
    )
    return _svg(width, top + box_height + 36, "\n".join(parts) + "\n" + caption + "\n", "pipeline")


def terminal(text: str, title: str) -> str:
    """Typeset real program output as a terminal window."""
    lines = text.rstrip("\n").split("\n")
    columns = max(len(line) for line in lines)
    width = columns * CHAR_WIDTH + PADDING * 2
    height = len(lines) * LINE_HEIGHT + PADDING * 2 + 26

    parts = [
        f'<rect width="{width:.0f}" height="{height:.0f}" rx="6" fill="{TERMINAL_BG}"/>',
        f'<path d="M0 26 H{width:.0f}" stroke="#21262d" stroke-width="1"/>',
    ]
    parts.extend(f'<circle cx="{18 + dot * 15}" cy="13" r="4" fill="#30363d"/>' for dot in range(3))
    parts.append(
        f'<text x="{width / 2:.0f}" y="17" text-anchor="middle" font-family="{SANS}" '
        f'font-size="10" fill="{TERMINAL_DIM}">binfer --self-test</text>'
    )

    for index, line in enumerate(lines):
        colour = TERMINAL_DIM if line.startswith("  KEY") or "  binfer " in line else TERMINAL_FG
        parts.append(
            f'<text x="{PADDING}" y="{26 + PADDING + 13 + index * LINE_HEIGHT}" '
            f'font-family="{MONO}" font-size="{FONT_SIZE}" fill="{colour}" '
            f'xml:space="preserve">{escape(line)}</text>'
        )
    return _svg(width, height, "\n".join(parts) + "\n", title)


def main() -> int:
    """Write the three images and report what changed."""
    OUT.mkdir(parents=True, exist_ok=True)
    images = {
        "logo.svg": logo(),
        "pipeline.svg": pipeline(),
        "report.svg": terminal(render_scorecard(score_all()), "binfer --self-test output"),
    }
    for name, content in images.items():
        path = OUT / name
        previous = path.read_text(encoding="utf-8") if path.exists() else None
        path.write_text(content, encoding="utf-8", newline="\n")
        state = "unchanged" if previous == content else "written"
        print(f"{state:>9}  {path.relative_to(ROOT)}  ({len(content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
