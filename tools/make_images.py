"""Generate the three SVG images the README embeds.

Nothing here is a mock-up. ``report.svg`` is produced by running the tool's own
self test and typesetting the text it actually printed, so the picture in the
README cannot claim output the program does not produce.

All three are monochrome, use no gradients and no raster assets. The line art
swaps two colours under ``prefers-color-scheme`` so it stays legible on a light
or a dark page; the terminal picture is a dark window on both.
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

# One fixed grey cannot serve both GitHub themes: anything dark enough to read
# on white falls to about 1.5:1 on the dark page. The line art carries a style
# block instead and swaps two colours under prefers-color-scheme, which the
# browser applies even when the SVG is loaded through an <img> tag.
INK_LIGHT, STRONG_LIGHT = "#57606a", "#24292f"
INK_DARK, STRONG_DARK = "#9198a1", "#e6edf3"

# The terminal picture is a dark window on both themes, the way a terminal is.
TERMINAL_BG = "#0d1117"
TERMINAL_FG = "#c9d1d9"
TERMINAL_DIM = "#8b949e"

THEME_STYLE = f"""<style>
  .ink {{ fill: {INK_LIGHT}; }}
  .ink-stroke {{ stroke: {INK_LIGHT}; fill: none; }}
  .strong {{ fill: {STRONG_LIGHT}; }}
  @media (prefers-color-scheme: dark) {{
    .ink {{ fill: {INK_DARK}; }}
    .ink-stroke {{ stroke: {INK_DARK}; }}
    .strong {{ fill: {STRONG_DARK}; }}
  }}
</style>
"""

MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
SANS = "ui-sans-serif, system-ui, Segoe UI, Helvetica, Arial, sans-serif"

FONT_SIZE = 13
CHAR_WIDTH = FONT_SIZE * 0.6
LINE_HEIGHT = 18
PADDING = 18


def _svg(width: float, height: float, body: str, title: str, *, themed: bool = False) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="{escape(title)}">\n'
        f"<title>{escape(title)}</title>\n{THEME_STYLE if themed else ''}{body}</svg>\n"
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
                    f'<rect class="strong" x="{x}" y="{y}" width="{cell}" height="{cell}"/>'
                )
            else:
                squares.append(
                    f'<rect class="ink-stroke" x="{x + 0.5}" y="{y + 0.5}" '
                    f'width="{cell - 1}" height="{cell - 1}" stroke-width="1"/>'
                )

    grid_width = columns * (cell + gap) - gap
    text_x = 20 + grid_width + 22
    body = (
        "\n".join(squares)
        + f'\n<text class="strong" x="{text_x}" y="{20 + rows * (cell + gap) - gap - 12}" '
        f'font-family="{MONO}" font-size="34" letter-spacing="-0.5">binfer</text>\n'
        f'<text class="ink" x="{text_x + 2}" y="{20 + rows * (cell + gap) - gap + 8}" '
        f'font-family="{SANS}" font-size="11">structure inference for unknown binaries</text>\n'
    )
    return _svg(text_x + 268, 20 + rows * (cell + gap) - gap + 22, body, "binfer", themed=True)


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
            f'<rect class="ink-stroke" x="{x}" y="{top}" width="{box_width}" '
            f'height="{box_height}" rx="4" stroke-width="1"/>'
        )
        parts.append(
            f'<text class="strong" x="{x + 12}" y="{top + 22}" font-family="{MONO}" '
            f'font-size="12">{escape(title)}</text>'
        )
        parts.extend(
            f'<text class="ink" x="{x + 12}" y="{top + 42 + line_index * 14}" '
            f'font-family="{SANS}" font-size="9.5">{escape(line)}</text>'
            for line_index, line in enumerate(lines)
        )
        if index + 1 < len(STAGES):
            arrow_x = x + box_width
            middle = top + box_height / 2
            parts.append(
                f'<path class="ink-stroke" d="M{arrow_x + 3} {middle} H{arrow_x + gap - 6}" '
                f'stroke-width="1"/>'
            )
            parts.append(
                f'<path class="ink" d="M{arrow_x + gap - 6} {middle - 3.5} '
                f'L{arrow_x + gap - 1} {middle} L{arrow_x + gap - 6} {middle + 3.5} Z"/>'
            )

    width = left * 2 + len(STAGES) * box_width + (len(STAGES) - 1) * gap
    caption = (
        f'<text class="ink" x="{left}" y="{top + box_height + 22}" font-family="{SANS}" '
        f'font-size="10">Every stage may refuse to conclude. What no stage explains is '
        f"reported as unexplained, not filled in.</text>"
    )
    return _svg(
        width,
        top + box_height + 36,
        "\n".join(parts) + "\n" + caption + "\n",
        "pipeline",
        themed=True,
    )


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
