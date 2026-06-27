"""End-to-end test: OCR inventory counts, feed into solver, verify path cost
and check scarce-aspect avoidance."""
import sys, types
from pathlib import Path

cfg = types.ModuleType("src.utils.config")
class _C:
    disabled_aspects: list[str] = []
    aspect_cost_overrides: dict[str, int] = {}
    game_window_title = ""
    next_board_hotkey = None
cfg.get_global_config = lambda: _C()
cfg.Config = _C
sys.modules["src.utils.config"] = cfg
# Stub mouseactions/window
for name in ("mouseactions", "window"):
    m = types.ModuleType(f"src.utils.{name}")
    sys.modules[f"src.utils.{name}"] = m

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from src.utils.finder import find_frame, find_aspects_in_frame
from src.utils.count_ocr import ocr_all_counts
from src.utils.aspects import update_costs_from_inventory, aspect_costs
from src.solvers.ringsolver import solve as ringsolver_solve
from repro import build_hexgrid, validate_solution


def main(board_path: str):
    image = Image.open(board_path)
    pixels = image.load()
    arr = np.array(image)

    # 1. Read inventory counts
    counts: dict[str, int] = {}
    for frame_color in [(100, 123, 123), (200, 123, 123)]:
        try:
            frame = find_frame(image, frame_color)
        except Exception as e:
            print(f"frame {frame_color} failed: {e}")
            continue
        aspects = find_aspects_in_frame(frame, pixels)
        counts.update(ocr_all_counts(arr, aspects))

    print(f"\nOCR'd {len(counts)} aspect counts")
    # Show scarce ones (count <= 20)
    scarce = sorted([(c, n) for n, c in counts.items() if c <= 20])
    print("Scarcest aspects (count <= 20):")
    for c, n in scarce:
        print(f"  {n:14s} = {c}")

    # 2. Update solver costs
    update_costs_from_inventory(counts)
    # Show the new costs for scarce aspects
    print("\nNew costs for scarce aspects:")
    for c, n in scarce:
        print(f"  {n:14s} count={c}  cost={aspect_costs[n]}")
    # Also show costs for aer, victus, aqua, lux — common routing picks
    for name in ["aer", "aqua", "terra", "ordo", "ignis", "perditio",
                 "victus", "lux", "tempestas", "motus", "vacuos"]:
        cnt = counts.get(name, "-")
        print(f"  {name:14s} count={cnt}  cost={aspect_costs.get(name, '-')}")

    # 3. Solve with new costs
    grid = build_hexgrid(Path(board_path))
    starts = [c for (c, a) in grid if a not in ("Free", "Missing")]
    print(f"\nInitials: {[(c, grid.get_value(c)) for c in starts]}")
    solved = ringsolver_solve(grid, starts)

    # 4. Report
    print(f"\nSolution cost: {solved.calculate_cost()}, {len(solved.applied_paths)} paths")
    from collections import Counter
    aspect_usage = Counter()
    for path in solved.applied_paths:
        # Count only middle cells (path[1:-1]) — the endpoints are initial
        for elem, _coord in path[1:-1]:
            aspect_usage[elem] += 1
    print("Aspect usage (middle cells only):")
    for aspect, n in sorted(aspect_usage.items(), key=lambda x: -x[1]):
        inv = counts.get(aspect, "-")
        print(f"  {aspect:14s} used={n}  available={inv}")

    for i, path in enumerate(solved.applied_paths):
        print(f"  path {i}: " + " → ".join(f"{a}" for a, _ in path))

    errs = validate_solution(grid, solved)
    incompat = [e for e in errs if e.startswith("INCOMPATIBLE")]
    print(f"\nIncompatibilities: {len(incompat)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "test_inputs/board_XFHZ5L.png")
