"""PDF to ordered text lines with layout coordinates.

Responsibilities, in order:
1. Pull lines with bounding boxes from every page.
2. Put them in reading order for one- or two-column pages.
3. Merge fragments that sit on the same visual row (a label and its text often
   arrive as separate lines).
4. Drop running headers and footers, detected by repetition across pages rather
   than by any form-specific pattern.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import pymupdf

_WS = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")
ROW_TOLERANCE = 2.5  # points; lines whose y0 differ by less are one row
MARGIN_FRACTION = 0.09  # top/bottom band where headers and footers live


@dataclass(slots=True)
class Line:
    text: str
    page: int
    col: int
    x0: float
    y0: float
    x1: float
    y1: float


def _norm(text: str) -> str:
    return _DIGITS.sub("#", _WS.sub(" ", text)).strip().lower()


def extract_lines(pdf_path: str | Path, *, strip_repeating: bool = True) -> tuple[list[Line], int]:
    """Return (lines in reading order, page count)."""
    doc = pymupdf.open(str(pdf_path))
    lines: list[Line] = []
    heights: dict[int, float] = {}
    for pno, page in enumerate(doc, start=1):
        mid = page.rect.width / 2
        heights[pno] = page.rect.height
        page_lines: list[Line] = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for ln in block["lines"]:
                text = "".join(s["text"] for s in ln["spans"])
                if not text.strip():
                    continue
                x0, y0, x1, y1 = ln["bbox"]
                page_lines.append(Line(text, pno, 0 if x0 < mid else 1, x0, y0, x1, y1))
        lines.extend(_merge_rows(_reading_order(page_lines, mid)))
    if strip_repeating:
        lines = strip_repeating_lines(lines, heights)
    return lines, len(doc)


def _reading_order(page_lines: list[Line], mid: float) -> list[Line]:
    """Order lines for a page that may mix two-column text with full-width lines.

    A line that straddles the page centre (a centred section heading, a
    full-width notice) breaks the page into bands. Within a band the left
    column is read top to bottom, then the right column. Bands are read in
    page order, each led by the spanning line that opened it.
    """
    spanning = sorted((l for l in page_lines if l.x0 < mid - 5 and l.x1 > mid + 5), key=lambda l: l.y0)
    span_ys = [l.y0 for l in spanning]

    def band_of(l: Line) -> int:
        return sum(1 for y in span_ys if y < l.y0 - ROW_TOLERANCE)

    def key(l: Line):
        if l.x0 < mid - 5 and l.x1 > mid + 5:
            return (span_ys.index(l.y0) + 1, 0, 0, round(l.y0 / ROW_TOLERANCE), l.x0)
        return (band_of(l), 1, l.col, round(l.y0 / ROW_TOLERANCE), l.x0)

    return sorted(page_lines, key=key)


def _merge_rows(lines: list[Line]) -> list[Line]:
    """Join lines that share a page, column and baseline into one line."""
    out: list[Line] = []
    for ln in lines:
        if out:
            prev = out[-1]
            same_row = (
                prev.page == ln.page
                and prev.col == ln.col
                and abs(prev.y0 - ln.y0) <= ROW_TOLERANCE
                and ln.x0 >= prev.x1 - 1
            )
            if same_row:
                out[-1] = replace(
                    prev,
                    text=prev.text.rstrip() + " " + ln.text.lstrip(),
                    x1=max(prev.x1, ln.x1),
                    y1=max(prev.y1, ln.y1),
                )
                continue
        out.append(ln)
    return out


def strip_repeating_lines(lines: list[Line], heights: dict[int, float]) -> list[Line]:
    """Drop lines in the top/bottom margin band whose normalised text recurs on many pages."""
    pages_by_key: dict[str, set[int]] = defaultdict(set)
    for ln in lines:
        pages_by_key[_norm(ln.text)].add(ln.page)
    npages = len(heights)
    threshold = max(3, int(0.4 * npages))
    out: list[Line] = []
    for ln in lines:
        h = heights[ln.page]
        in_margin = ln.y0 < MARGIN_FRACTION * h or ln.y1 > (1 - MARGIN_FRACTION) * h
        if in_margin and len(pages_by_key[_norm(ln.text)]) >= threshold:
            continue
        out.append(ln)
    return out
