"""Run the solver against every test input, validate each, and summarise."""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

# Same stubbing as repro.py
import types
cfg = types.ModuleType("src.utils.config")
class _C:
    disabled_aspects: list[str] = []
    aspect_cost_overrides: dict[str, int] = {}
    game_window_title = ""
    next_board_hotkey = None
cfg.get_global_config = lambda: _C()
cfg.Config = _C
sys.modules["src.utils.config"] = cfg

ma = types.ModuleType("src.utils.mouseactions")
for n in ("drag_mouse_from_to", "place_all_aspects", "place_aspect_at",
         "craft_inventory_aspect", "craft_missing_inventory_aspects"):
    setattr(ma, n, lambda *a, **k: None)
sys.modules["src.utils.mouseactions"] = ma

wn = types.ModuleType("src.utils.window")
for n in ("find_game", "screenshot_window", "add_offset", "gui"):
    setattr(wn, n, lambda *a, **k: None)
sys.modules["src.utils.window"] = wn

sys.path.insert(0, str(Path(__file__).parent))

from repro import build_hexgrid, validate_solution  # noqa: E402
from src.solvers.ringsolver import solve as ringsolver_solve  # noqa: E402


def run_one(board_path: Path) -> dict:
    t0 = time.time()
    try:
        grid = build_hexgrid(board_path)
    except Exception as e:
        return {"file": board_path.name, "status": "parse_error", "error": str(e)}
    starts = [c for (c, a) in grid if a not in ("Free", "Missing")]
    try:
        solved = ringsolver_solve(grid, starts)
    except Exception as e:
        return {"file": board_path.name, "status": "solver_error",
                "error": f"{type(e).__name__}: {e}"}
    errs = validate_solution(grid, solved)
    incompat = [e for e in errs if e.startswith("INCOMPATIBLE")]
    overwrites = [e for e in errs if e.startswith("OVERWRITE")]
    return {
        "file": board_path.name,
        "status": "ok" if not errs else "invalid",
        "cost": solved.calculate_cost(),
        "n_paths": len(solved.applied_paths),
        "n_starts": len(starts),
        "n_incompatible": len(incompat),
        "n_overwrite": len(overwrites),
        "time_ms": (time.time() - t0) * 1000,
    }


def main():
    dir_ = Path("test_inputs")
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir():
        dir_ = Path(sys.argv[1])
    inputs = sorted(dir_.glob("board_*.png"))
    # Optional name-filter as second arg
    if len(sys.argv) > 2:
        needle = sys.argv[2]
        inputs = [p for p in inputs if needle in p.name]
    total = len(inputs)
    print(f"Running {total} boards...")
    results = []
    for i, p in enumerate(inputs):
        print(f"  [{i+1}/{total}] {p.name} ...", end="", flush=True)
        r = run_one(p)
        print(f"  {r['status']}  incompat={r.get('n_incompatible', '?')}")
        results.append(r)

    # Summary
    ok = [r for r in results if r["status"] == "ok"]
    invalid = [r for r in results if r["status"] == "invalid"]
    solver_err = [r for r in results if r["status"] == "solver_error"]
    parse_err = [r for r in results if r["status"] == "parse_error"]
    print(f"\n== Summary ==")
    print(f"  OK:             {len(ok)}/{total}")
    print(f"  INVALID:        {len(invalid)}  (solved but with violations)")
    print(f"  SOLVER ERROR:   {len(solver_err)}")
    print(f"  PARSE ERROR:    {len(parse_err)}")
    if invalid:
        print("\nBoards still producing invalid solutions:")
        for r in invalid[:20]:
            print(f"  {r['file']}  incompat={r['n_incompatible']}  "
                  f"overwrite={r['n_overwrite']}  cost={r['cost']}")
    if solver_err:
        print("\nSolver errors:")
        for r in solver_err[:10]:
            print(f"  {r['file']}  {r['error']}")


if __name__ == "__main__":
    main()
