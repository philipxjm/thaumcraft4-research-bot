"""OCR the inventory-count text next to each aspect block.

The bot previously had no inventory-count awareness: the solver treats every
aspect as equally available, so it happily routes through aspects you only
have one of. This module extracts counts from the rendered inventory panel
so the solver can weight scarce aspects higher.

Approach: template match 0-9 against the pure-white text pixels in the cell.
The bot's custom resource pack renders the aspect as a solid-colour block in
the top-left of each slot, and Minecraft draws the stack count as white-with-
shadow pixel text in the bottom-right. Both the block and the count live in
the same inventory slot.

Templates were extracted from a 2x GUI-scale screenshot. Other scales are
handled by normalising extracted digits to the template's pixel height before
comparison.
"""

from __future__ import annotations

from typing import Tuple, Iterable
import numpy as np


# ---------- digit templates ------------------------------------------------
#
# Each is a boolean bitmap of the pure-white pixels of one Minecraft default-
# font digit at 2x GUI scale. Produced from `board_guObMF.png`. The shadow
# pixels are excluded; we match on the foreground glyph only.

_DIGITS_RAW: dict[str, list[str]] = {
    "0": [
        "...XXXXXXX.",
        "...XXXXXXX.",
        "XX........X",
        "XX........X",
        "XX........X",
        "XX......XXX",
        "XX......XXX",
        "XX...XX...X",
        "XX...XX...X",
        "XX........X",
        "XXXXX.....X",
        "XXXXX.....X",
        "XX........X",
        "XX........X",
        "...........",
        "...XXXXXXX.",
        "...XXXXXXX.",
    ],
    "1": [
        ".....XX.....",
        ".....XX.....",
        "...XXXX.....",
        "...XXXX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        "XXXXXXXXXXXX",
        "XXXXXXXXXXXX",
    ],
    "2": [
        "...XXXXXXX..",
        "...XXXXXXX..",
        "XX........XX",
        "XX........XX",
        "..........XX",
        "..........XX",
        "..........XX",
        ".....XXXXX..",
        ".....XXXXX..",
        "............",
        "...XX.......",
        "...XX.......",
        "XX........XX",
        "XX........XX",
        "XX........XX",
        "XXXXXXXXXXXX",
        "XXXXXXXXXXXX",
    ],
    "3": [
        "...XXXXXXX.",
        "...XXXXXXX.",
        "XX........X",
        "XX........X",
        "..........X",
        "..........X",
        "..........X",
        ".....XXXXX.",
        ".....XXXXX.",
        "...........",
        "..........X",
        "..........X",
        "XX........X",
        "XX........X",
        "...........",
        "...XXXXXXX.",
        "...XXXXXXX.",
    ],
    "4": [
        "........XXXX",
        "........XXXX",
        ".....XX...XX",
        ".....XX...XX",
        "..........XX",
        "...XX.....XX",
        "...XX.....XX",
        "XX........XX",
        "XX........XX",
        "XX........XX",
        "XXXXXXXXXXXX",
        "XXXXXXXXXXXX",
        "..........XX",
        "..........XX",
        "..........XX",
        "..........XX",
        "..........XX",
    ],
    "5": [
        "XXXXXXXXXXXX",
        "XXXXXXXXXXXX",
        "XX..........",
        "XX..........",
        "XX..........",
        "XXXXXXXXXX..",
        "XXXXXXXXXX..",
        "..........XX",
        "..........XX",
        "..........XX",
        "..........XX",
        "..........XX",
        "XX........XX",
        "XX........XX",
        "............",
        "...XXXXXXX..",
        "...XXXXXXX..",
    ],
    "6": [
        ".....XXXXX..",
        ".....XXXXX..",
        "...XX.......",
        "...XX.......",
        "............",
        "XX..........",
        "XX..........",
        "XXXXXXXXXX..",
        "XXXXXXXXXX..",
        "XX..........",
        "XX........XX",
        "XX........XX",
        "XX........XX",
        "XX........XX",
        "............",
        "...XXXXXXX..",
        "...XXXXXXX..",
    ],
    "7": [
        "XXXXXXXXXXXX",
        "XXXXXXXXXXXX",
        "XX........XX",
        "XX........XX",
        "..........XX",
        "..........XX",
        "..........XX",
        "........XX..",
        "........XX..",
        "............",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
    ],
    "8": [
        "...XXXXXXX.",
        "...XXXXXXX.",
        "XX........X",
        "XX........X",
        "XX........X",
        "XX........X",
        "XX........X",
        "...XXXXXXX.",
        "...XXXXXXX.",
        "...........",
        "XX........X",
        "XX........X",
        "XX........X",
        "XX........X",
        "...........",
        "...XXXXXXX.",
        "...XXXXXXX.",
    ],
    "9": [
        "...XXXXXXX.",
        "...XXXXXXX.",
        "XX........X",
        "XX........X",
        "XX........X",
        "XX........X",
        "XX........X",
        "...XXXXXXXX",
        "...XXXXXXXX",
        "..........X",
        "..........X",
        "..........X",
        "........XX.",
        "........XX.",
        "...........",
        "...XXXX....",
        "...XXXX....",
    ],
}


def _as_bitmap(rows: list[str]) -> np.ndarray:
    return np.array([[c == "X" for c in row] for row in rows], dtype=bool)


_TEMPLATES: dict[str, np.ndarray] = {k: _as_bitmap(v) for k, v in _DIGITS_RAW.items()}
TEMPLATE_HEIGHT = 17  # all templates are 17 rows tall


# ---------- core OCR -------------------------------------------------------


def _white_mask(rgb: np.ndarray) -> np.ndarray:
    """Pure-white pixel mask (the foreground glyph colour). Shadow and panel
    grey are excluded."""
    return (rgb[..., 0] == 255) & (rgb[..., 1] == 255) & (rgb[..., 2] == 255)


def _tight_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Return (y0, y1, x0, x1) inclusive of True cells, or None if empty."""
    ys = np.where(mask.any(axis=1))[0]
    xs = np.where(mask.any(axis=0))[0]
    if ys.size == 0 or xs.size == 0:
        return None
    return int(ys[0]), int(ys[-1]), int(xs[0]), int(xs[-1])


def _split_into_digits(mask: np.ndarray, min_gap: int = 3) -> list[np.ndarray]:
    """Split a horizontal run of glyph pixels into per-digit bitmaps.

    Minecraft's default font has within-digit column gaps (for example column
    2 of `3` is fully empty) so we can't split on single-column gaps. The
    real inter-digit gap is ≥3 empty columns, while within-digit gaps are
    always ≤2. That gives a clean cutoff.
    """
    col = mask.any(axis=0)
    n = len(col)
    # Mark true cuts: runs of ≥min_gap empty columns
    cuts: list[tuple[int, int]] = []
    in_gap = False
    gap_start = 0
    for i, c in enumerate(col):
        if not c and not in_gap:
            in_gap = True
            gap_start = i
        elif c and in_gap:
            in_gap = False
            if i - gap_start >= min_gap:
                cuts.append((gap_start, i))
    # Treat end-of-mask as a potential gap closure (no action needed — we
    # just need the cut boundaries to slice the glyph span).

    # Use cuts to slice the glyph-spanning range [first_nonempty, last+1)
    nonempty = np.where(col)[0]
    if nonempty.size == 0:
        return []
    start = int(nonempty[0])
    end = int(nonempty[-1]) + 1

    boundaries = [start]
    for a, b in cuts:
        if a >= start and b <= end:
            boundaries.append(a)
            boundaries.append(b)
    boundaries.append(end)

    digits: list[np.ndarray] = []
    for i in range(0, len(boundaries), 2):
        a, b = boundaries[i], boundaries[i + 1]
        if b <= a:
            continue
        sub = mask[:, a:b]
        bbox = _tight_bbox(sub)
        if bbox is None:
            continue
        y0, y1, x0, x1 = bbox
        digits.append(sub[y0:y1 + 1, x0:x1 + 1])
    return digits


def _resize_bitmap_to(bm: np.ndarray, target_h: int) -> np.ndarray:
    """Nearest-neighbour resize of a boolean bitmap preserving aspect ratio."""
    h, w = bm.shape
    if h == target_h:
        return bm
    scale = target_h / h
    target_w = max(1, int(round(w * scale)))
    # Re-sample by integer indexing
    ys = np.clip(np.round(np.arange(target_h) / scale).astype(int), 0, h - 1)
    xs = np.clip(np.round(np.arange(target_w) / scale).astype(int), 0, w - 1)
    return bm[np.ix_(ys, xs)]


def _classify_digit(bm: np.ndarray) -> tuple[str, float]:
    """Match bm against the 10 digit templates. Returns (digit, similarity)
    where similarity is the fraction of pixels matching the best template."""
    # Normalise input to template height
    bm_n = _resize_bitmap_to(bm, TEMPLATE_HEIGHT)
    best_digit = "?"
    best_score = -1.0
    for d, tmpl in _TEMPLATES.items():
        th, tw = tmpl.shape
        # Pad or crop bm_n's width to match template width
        w_cur = bm_n.shape[1]
        if w_cur == tw:
            cand = bm_n
        elif w_cur < tw:
            # center-pad
            pad_l = (tw - w_cur) // 2
            pad_r = tw - w_cur - pad_l
            cand = np.pad(bm_n, ((0, 0), (pad_l, pad_r)))
        else:
            # center-crop
            off = (w_cur - tw) // 2
            cand = bm_n[:, off:off + tw]
        matches = int(np.sum(cand == tmpl))
        score = matches / (th * tw)
        if score > best_score:
            best_score = score
            best_digit = d
    return best_digit, best_score


def ocr_region(rgb: np.ndarray) -> tuple[int | None, float]:
    """OCR a crop that's expected to contain a single number.

    Returns (value, min_confidence) or (None, 0.0) if no digits were found.
    `min_confidence` is the worst per-digit match quality in [0, 1]; values
    below ~0.85 usually mean the crop didn't actually contain a number.
    """
    mask = _white_mask(rgb)
    bbox = _tight_bbox(mask)
    if bbox is None:
        return None, 0.0
    y0, y1, x0, x1 = bbox
    tight = mask[y0:y1 + 1, x0:x1 + 1]
    digits = _split_into_digits(tight)
    if not digits:
        return None, 0.0
    chars = []
    min_conf = 1.0
    for d in digits:
        ch, conf = _classify_digit(d)
        chars.append(ch)
        min_conf = min(min_conf, conf)
    if any(c == "?" for c in chars):
        return None, min_conf
    try:
        return int("".join(chars)), min_conf
    except ValueError:
        return None, min_conf


def ocr_all_counts(image_rgb: np.ndarray,
                   inventory_aspects: Iterable[
                       tuple[tuple[int, int, int, int], str]],
                   min_block_side: int = 30,
                   min_confidence: float = 0.90,
                   ) -> dict[str, int]:
    """Run :func:`ocr_aspect_cell` on each aspect in the inventory and return
    ``{aspect_name: count}``.

    ``inventory_aspects`` is the output of ``finder.find_aspects_in_frame``.
    That function also reports false positives for aspects whose colour
    matches white text (notably *tempestas*); we filter on block size.
    Entries whose OCR confidence is below ``min_confidence`` are dropped.
    """
    out: dict[str, int] = {}
    for bbox, name in inventory_aspects:
        x0, y0, x1, y1 = bbox
        if (x1 - x0) < min_block_side or (y1 - y0) < min_block_side:
            continue
        count, conf = ocr_aspect_cell(image_rgb, bbox)
        if count is None or conf < min_confidence:
            continue
        # If the same aspect appears twice (e.g. tempestas matched via white
        # digit blobs), keep the higher-confidence read — but since we already
        # filter small bboxes, duplicates are rare.
        if name in out:
            continue
        out[name] = count
    return out


def ocr_aspect_cell(image_rgb: np.ndarray,
                    aspect_bbox: Tuple[int, int, int, int]
                    ) -> tuple[int | None, float]:
    """Given the bounding box of an aspect's coloured "L-shaped" block, OCR
    the count displayed in the same inventory slot.

    The "L" occupies the top ~75% and left ~75% of the cell's square footprint,
    leaving a ~25%×25% cut-out at the bottom-right where the count digits are
    drawn. The block bbox we receive is the tight bounding box of the coloured
    pixels — i.e., it already contains that cut-out inside its right-bottom
    corner.
    """
    x0, y0, x1, y1 = aspect_bbox
    bw = x1 - x0
    bh = y1 - y0
    # Count digits start inside the bottom-right of the bbox but a long count
    # (3 digits) can extend to the left of the bbox centre and a few pixels
    # past the right edge. Take the lower half of the bbox and extend a
    # little left and right. Staying within ~one bbox width avoids bleeding
    # into neighbouring cells.
    sx0 = max(0, x0 + bw // 3)
    sx1 = min(image_rgb.shape[1], x1 + bw // 6)
    sy0 = y0 + bh // 2
    sy1 = min(image_rgb.shape[0], y1 + bh // 10)
    crop = image_rgb[sy0:sy1, sx0:sx1]
    return ocr_region(crop)
