"""Reproduce the solver on a given test board without the mouse/keyboard/display
dependencies of __main__.py. Dumps the parsed grid, runs the solver, then
validates the resulting applied_paths against real TC4 adjacency rules.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# Stub the config module (pulls in Windows-only deps otherwise)
cfg = types.ModuleType("src.utils.config")
class _C:
    disabled_aspects: list[str] = []
    aspect_cost_overrides: dict[str, int] = {}
    game_window_title = ""
    next_board_hotkey = None
cfg.get_global_config = lambda: _C()
cfg.Config = _C
sys.modules["src.utils.config"] = cfg

# Stub mouseactions (imports pyautogui)
ma = types.ModuleType("src.utils.mouseactions")
for n in ("drag_mouse_from_to", "place_all_aspects", "place_aspect_at",
         "craft_inventory_aspect", "craft_missing_inventory_aspects"):
    setattr(ma, n, lambda *a, **k: None)
sys.modules["src.utils.mouseactions"] = ma

# Stub window (imports pyautogui)
wn = types.ModuleType("src.utils.window")
for n in ("find_game", "screenshot_window", "add_offset", "gui"):
    setattr(wn, n, lambda *a, **k: None)
sys.modules["src.utils.window"] = wn

# Now we can import the rest
from PIL import Image  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from src.utils.finder import (  # noqa: E402
    find_frame, find_aspects_in_frame, find_squares_in_frame,
)
from src.utils.grid import HexGrid  # noqa: E402
from src.utils.aspects import aspect_graph, aspect_parents  # noqa: E402
from src.solvers.ringsolver import solve as ringsolver_solve  # noqa: E402


def analyze_image_board(image):
    pixels = image.load()
    board = find_frame(image, (150, 123, 123))
    board_aspects = find_aspects_in_frame(board, pixels)
    empty_hexagons = find_squares_in_frame(board, pixels, (195, 195, 195))
    return board_aspects, empty_hexagons


def group_hexagons(empty_hexagons, board_aspects, image_height):
    from collections import defaultdict
    from src.utils.finder import find_close_x_in_grouped

    grouped = defaultdict(list)
    for x, y in empty_hexagons:
        x = find_close_x_in_grouped(x, grouped, 3)
        grouped[x].append((x, y, "Free"))
    for box_coords, name in board_aspects:
        left, top, right, bottom = box_coords
        x = int((right + left) / 2)
        y = (bottom + top) / 2
        x = find_close_x_in_grouped(x, grouped, 3)
        grouped[x].append((x, y, name))

    grouped_items = sorted(grouped.items(), key=lambda e: e[0])
    columns = []
    smallest_y_diff = image_height
    for _, coords in grouped_items:
        coords.sort(key=lambda c: c[1])
        if len(coords) == 1:
            columns.append([coords[0]])
            continue
        difference_y = min(abs(coords[i + 1][1] - coords[i][1])
                           for i in range(len(coords) - 1))
        if smallest_y_diff > difference_y:
            smallest_y_diff = difference_y
        column = [coords[0]]
        for i in range(len(coords) - 1):
            curr_diff = coords[i + 1][1] - column[-1][1]
            while curr_diff > 1.5 * difference_y:
                column.append((coords[i][0], coords[i][1] + difference_y, "Missing"))
                curr_diff -= difference_y
            column.append(coords[i + 1])
        columns.append(column)

    valid_y_coords: list[float] = []
    for col in columns:
        for x, y, _ in col:
            if any(abs(e - y) < max(smallest_y_diff / 4, 5)
                   for e in valid_y_coords):
                continue
            valid_y_coords.append(y)
    valid_y_coords.sort()
    for i in range(len(valid_y_coords) - 1):
        if valid_y_coords[i + 1] - valid_y_coords[i] > 0.75 * smallest_y_diff:
            valid_y_coords.append(valid_y_coords[i] + smallest_y_diff * 0.5)
    valid_y_coords.sort()
    return columns, valid_y_coords, smallest_y_diff


def build_hexgrid(image_path: Path) -> HexGrid:
    image = Image.open(image_path)
    board_aspects, empty = analyze_image_board(image)
    columns, valid_y, diff = group_hexagons(empty, board_aspects, image.height)
    grid = HexGrid()
    for x_index, col in enumerate(columns):
        for x, y, value in col:
            y_index = -1
            for i, cy in enumerate(valid_y):
                if abs(cy - y) < max(diff / 4, 5):
                    y_index = i
                    break
            if y_index == -1:
                raise RuntimeError(f"bad y {y} vs {valid_y}")
            grid.set_hex((x_index, y_index), value, (x, y))
    return grid


# ---------- validation ------------------------------------------------------


def validate_solution(grid: HexGrid, solved) -> list[str]:
    """Return a list of validity-violation strings for the solved grid."""
    errs: list[str] = []

    # Build final cell->aspect map (initial + applied)
    cell_aspect: dict[tuple[int, int], str] = {
        coord: aspect for coord, (aspect, _) in grid.grid.items()
    }
    # Which path each cell came from (for error reporting)
    cell_origin: dict[tuple[int, int], str] = {c: "initial" for c in cell_aspect}
    for pi, path in enumerate(solved.applied_paths):
        for el, coord in path:
            prev_aspect = cell_aspect.get(coord)
            # Only flag as "overwrite" if a real (non-Free/Missing) aspect is
            # replaced with a different aspect.
            if (prev_aspect not in (None, "Free", "Missing")
                    and prev_aspect != el):
                errs.append(
                    f"OVERWRITE: PATH{pi} at {coord}: "
                    f"{prev_aspect} (from {cell_origin[coord]}) -> {el}")
            cell_aspect[coord] = el
            cell_origin[coord] = f"path{pi}"

    # For every filled cell, check compatibility with every hex-neighbor
    def compatible(a: str, b: str) -> bool:
        if a == b:
            return True
        return b in aspect_graph.get(a, [])

    for coord, aspect in cell_aspect.items():
        if aspect in ("Free", "Missing", None):
            continue  # nothing placed here
        for nb in grid.get_neighbors(coord):
            nb_aspect = cell_aspect.get(nb)
            if nb_aspect in (None, "Free", "Missing"):
                continue  # empty neighbor
            if not compatible(aspect, nb_aspect):
                errs.append(
                    f"INCOMPATIBLE: {coord}({aspect} from {cell_origin[coord]}) "
                    f"↔ {nb}({nb_aspect} from {cell_origin[nb]})")
    return errs


def main(board_path: str) -> None:
    grid = build_hexgrid(Path(board_path))

    start_aspects = [coord for (coord, aspect) in grid
                     if aspect not in ("Free", "Missing")]
    print(f"Board has {len(grid.grid)} cells, "
          f"{len(start_aspects)} initial aspects: "
          f"{sorted((c, grid.get_value(c)) for c in start_aspects)}")

    solved = ringsolver_solve(grid, start_aspects)
    print(f"\nSolver returned cost {solved.calculate_cost()}, "
          f"{len(solved.applied_paths)} applied paths")
    for i, path in enumerate(solved.applied_paths):
        print(f"  path {i}: " + " → ".join(f"{a}@{c}" for a, c in path))

    print("\n-- Validation --")
    errs = validate_solution(grid, solved)
    if not errs:
        print("All adjacencies compatible ✓")
    else:
        for e in errs:
            print("  " + e)
        # Deduplicate pairs and show unique incompatible adjacencies
        pairs = set()
        for e in errs:
            if e.startswith("INCOMPATIBLE"):
                pairs.add(frozenset([e.split("↔")[0], e.split("↔")[1]]))
        print(f"\n{len(errs)} total errors, {len(pairs)} unique incompatible adjacencies")


if __name__ == "__main__":
    main(sys.argv[1])
