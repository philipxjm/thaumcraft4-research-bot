from collections import defaultdict
from pathlib import Path
from PIL import ImageDraw
import PIL.Image
import time
import sys
import traceback
from time import sleep
import msvcrt
import keyboard


# Local libs
from .utils.window import *
from .utils.finder import *
from .utils.config import get_global_config
from .utils.grid import HexGrid, SolvingHexGrid, OnscreenAspect
from .utils.aspects import aspect_parents, _find_cheapest_element_paths_many, update_costs_from_inventory
from .utils.count_ocr import ocr_all_counts
from .solvers.ringsolver import solve as ringsolver_solve
from .utils.renderer import *
from .utils.log import log
from .utils.mouseactions import place_aspect_at, craft_inventory_aspect, craft_missing_inventory_aspects, \
    place_all_aspects

# Disable 0.1 seconds delay between each pyautogui call
gui.PAUSE = 0

MODE = sys.argv[1] if len(sys.argv) > 1 else None
TEST_MODE = MODE == "test"  # Read debug_input and dont perform actions
TEST_ALL_MODE = MODE == "test_all"  # Run test for all collected test_inputs


def main():
    print("MODE=", MODE)
    config = get_global_config()
    if TEST_ALL_MODE:
        test_all_samples(config)
    elif TEST_MODE:
        solve_board(config, BotState(), test_mode=True)
    elif MODE == "console":
        console_mode(config)
    else:
        gui_mode(config)

    print("CacheInfo find cheapest element paths", _find_cheapest_element_paths_many.cache_info())
    print("CacheInfo calculate distance", HexGrid.calculate_distance.cache_info())
    print("CacheInfo get neighbors", HexGrid.get_neighbors.cache_info())

def dedupe_inventory_aspects(inventory_aspects: list[OnscreenAspect]) -> list[OnscreenAspect]:
    """Keep only the largest detected region per aspect name.

    White GUI text (aspect count digits) exactly matches tempestas' pure-white
    color, so the panel scan yields several phantom 'tempestas' entries; the
    real slot is always the biggest region. Placement grabs the first match,
    so a phantom first = dragging from a text label = nothing picked up.
    """
    best: dict[str, OnscreenAspect] = {}
    dropped = 0
    for box, name in inventory_aspects:
        area = (box[2] - box[0] + 1) * (box[3] - box[1] + 1)
        if name in best:
            dropped += 1
            prev_box = best[name][0]
            prev_area = (prev_box[2] - prev_box[0] + 1) * (prev_box[3] - prev_box[1] + 1)
            if area <= prev_area:
                continue
        best[name] = (box, name)
    if dropped:
        log.info("Dropped %d duplicate inventory detections (GUI text matching aspect colors)", dropped)
    return list(best.values())


class BotState:
    """Carries what survives between boards: the inventory panel layout and
    the last solution (for retrying placement)."""

    def __init__(self):
        self.inventory_aspects: list[OnscreenAspect] | None = None
        self.window_base_coords = None
        self.solved = None


def solve_board(config: Config, state: BotState, test_mode: bool = False):
    """One full board cycle: screenshot, parse, OCR counts, solve, render the
    debug image. Placement is separate (place_solution) so the GUI can retry."""
    t_start = time.time()
    image, window_base_coords = setup_image(
        test_mode, state.inventory_aspects is not None
    )

    pixels = image.load()
    t_shot = time.time()

    if test_mode:
        state.inventory_aspects = dedupe_inventory_aspects(analyze_image_inventory(image, pixels))
    elif state.inventory_aspects is None:
        state.inventory_aspects, needs_image_retake = find_and_create_inventory_aspects(image, pixels, window_base_coords)
        state.inventory_aspects = dedupe_inventory_aspects(state.inventory_aspects)
        log.info(
            "Inventory map (%d aspects): %s",
            len(state.inventory_aspects),
            ", ".join(
                f"{name}@{(box[0] + box[2]) // 2},{(box[1] + box[3]) // 2}"
                for box, name in state.inventory_aspects
            ),
        )
        # TODO: scuffed!
        if needs_image_retake:
            image, window_base_coords = setup_image(
                test_mode, state.inventory_aspects is not None
            )
            pixels = image.load()

    t_inv = time.time()
    grid = generate_hexgrid_from_image(image, pixels)
    t_parse = time.time()

    givens = [(c, a) for c, (a, _) in grid.grid.items() if a not in ("Free", "Missing")]
    log.info(
        "Parsed %d given aspects: %s",
        len(givens),
        ", ".join(f"{a}@{c[0]},{c[1]}" for c, a in givens),
    )

    save_input_image(image, grid)

    # OCR inventory counts and re-weight solver costs so scarce aspects
    # are avoided. This needs to happen every board because counts change
    # as the user places aspects.
    try:
        import numpy as np
        counts = ocr_all_counts(np.array(image), state.inventory_aspects)
        log.info("OCR'd %d aspect counts", len(counts))
        update_costs_from_inventory(counts, owned_aspects={n for _, n in state.inventory_aspects})
    except Exception:
        log.exception("Inventory OCR failed; proceeding with default costs")
    t_ocr = time.time()
    log.info(
        "timings: screenshot %.2fs, inventory %.2fs, board-parse %.2fs, ocr %.2fs",
        t_shot - t_start, t_inv - t_shot, t_parse - t_inv, t_ocr - t_parse,
    )

    draw = ImageDraw.Draw(image)
    try:
        solved = generate_solution_from_hexgrid(grid)
    except Exception as e:
        print("Failed to generate solution, dumping board interpretation debug render")
        draw_board_coords(grid, draw)
        image.save("debug_render.png")
        raise e

    for path in solved.applied_paths:
        draw_board_path(image, solved, path)
        draw_placing_hints(
            image, draw, grid, state.inventory_aspects, path
        )

    draw_board_coords(solved, draw)

    image.save("debug_render.png")

    # Log the plan: exactly which aspects go on which cells. A "solved but
    # wrong in-game" report plus these lines pinpoints whether a chain link
    # or a placement is at fault.
    for i, path in enumerate(solved.applied_paths):
        log.info("path %d: %s", i, " -> ".join(f"{el}@{c[0]},{c[1]}" for el, c in path))
    if state.inventory_aspects is not None:
        owned = {name for _, name in state.inventory_aspects}
        given_names = {a for _, a in givens}
        needed = {el for path in solved.applied_paths for el, _ in path}
        unowned = needed - owned - given_names
        if unowned:
            log.warning(
                "Solution needs aspects not visible in your inventory: %s - "
                "crafting them is experimental and may fail silently",
                ", ".join(sorted(unowned)),
            )

    state.window_base_coords = window_base_coords
    state.solved = solved
    return solved


def place_solution(state: BotState):
    t0 = time.time()
    if not ensure_solution_aspects(state):
        raise Exception(
            "Could not craft all aspects this solution needs - see the log above "
            "for which craft failed. Craft them manually and press Retry placement."
        )
    t1 = time.time()
    place_all_aspects(state.window_base_coords, state.inventory_aspects, state.solved)
    t2 = time.time()
    verify_and_repair_placement(state)
    t3 = time.time()
    log.info("timings: craft-check %.2fs, place %.2fs, verify %.2fs", t1 - t0, t2 - t1, t3 - t2)


def compute_craft_plan(needed, owned: set, counts: dict) -> list[str] | None:
    """Derives the full craft sequence from a single inventory snapshot.

    Walks each needed aspect's parent tree, reserving stock (known OCR counts;
    aspects that are visible but unreadable count as plentiful; absent = 0)
    and appending a craft for every shortfall, parents before children.
    Returns None when a primal has run out - those can't be crafted.
    """
    stock = dict(counts)
    for aspect in owned:
        stock.setdefault(aspect, 999)

    plan: list[str] = []

    def acquire(aspect: str, qty: int, depth: int = 0) -> bool:
        if depth > 12:
            return False
        have = stock.get(aspect, 0)
        take = min(have, qty)
        stock[aspect] = have - take
        shortfall = qty - take
        if shortfall == 0:
            return True
        parent_a, parent_b = aspect_parents[aspect]
        if parent_a is None or parent_b is None:
            log.error("Primal aspect %s has run out (need %d more) - cannot craft it", aspect, shortfall)
            return False
        for _ in range(shortfall):
            if not acquire(parent_a, 1, depth + 1) or not acquire(parent_b, 1, depth + 1):
                return False
            plan.append(aspect)
        return True

    for aspect, amount in needed.items():
        if not acquire(aspect, amount):
            return None
    return plan


def ensure_solution_aspects(state: BotState) -> bool:
    """Crafts what the current solution needs before placing, planned entirely
    from one inventory snapshot. The panel is only re-scanned when a craft
    introduces a NEW aspect kind (the panel reflows to insert its slot,
    shifting later positions - same reason a kind vanishing at count 0 makes
    session-cached positions stale, which is why the snapshot here is fresh),
    plus once at the end so placement drags from current positions."""
    from collections import Counter
    import numpy as np

    needed = Counter()
    for path in state.solved.applied_paths:
        for aspect, _ in path[1:-1]:
            needed[aspect] += 1

    prev_owned = {name for _, name in state.inventory_aspects} if state.inventory_aspects else set()

    def scan(expect=frozenset()):
        # Research-point orbs from a just-completed board fly across the
        # aspect panel and cover slots; a scan taken mid-animation "loses"
        # aspects (once even a 945-stock primal). If anything we expect to
        # own is missing, wait out the animation and re-shoot.
        panel = []
        window_base_coords = None
        image = None
        for _ in range(3):
            image, window_base_coords = setup_image(False, True)
            panel = dedupe_inventory_aspects(analyze_image_inventory(image, image.load()))
            lost = set(expect) - {name for _, name in panel}
            if lost:
                log.info(
                    "Panel scan is missing %s (an animation may be covering the panel); rescanning...",
                    ", ".join(sorted(lost)),
                )
                sleep(1.2)
                continue
            break
        state.inventory_aspects = panel
        state.window_base_coords = window_base_coords
        try:
            counts = ocr_all_counts(np.array(image), panel)
        except Exception:
            counts = {}
        return panel, window_base_coords, counts, {name for _, name in panel}

    panel, window_base_coords, counts, owned = scan(expect=prev_owned)

    plan = compute_craft_plan(needed, owned, counts)
    if plan is None:
        return False
    if not plan:
        return True

    log.info("Craft plan (%d crafts): %s", len(plan), " -> ".join(plan))

    stale = False
    for aspect in plan:
        if stale:
            panel, window_base_coords, counts, owned = scan(expect=owned)
            stale = False
        parent_a, parent_b = aspect_parents[aspect]
        log.info("Crafting %s (%s + %s)", aspect, parent_a, parent_b)
        if not craft_inventory_aspect(window_base_coords, panel, aspect):
            return False
        sleep(0.4)
        if aspect not in owned:
            # First of its kind: the panel inserts a slot and later positions
            # shift, so the next craft needs fresh coordinates.
            owned.add(aspect)
            stale = True

    # Hand placement a current panel and confirm the plan worked.
    panel, window_base_coords, counts, owned = scan(expect=owned | set(needed))
    still_missing = [a for a in needed if a not in owned]
    if still_missing:
        log.error("Crafting finished but aspects are still missing: %s", ", ".join(still_missing))
        return False
    return True


def verify_and_repair_placement(state: BotState, attempts: int = 2):
    """Re-screenshot the board and re-drag any planned aspect that didn't
    land. Fast teleport-drags can miss on heavy clients: the game samples the
    cursor a frame late and the aspect never leaves the inventory, leaving a
    silent gap in a chain (placed aspects with no link between them)."""
    planned = {}
    for path in state.solved.applied_paths:
        for aspect, coord in path[1:-1]:
            planned[coord] = aspect

    if not planned:
        return

    for attempt in range(attempts + 1):
        sleep(0.4)
        image, window_base_coords = setup_image(False, True)
        try:
            image.save(f"debug_verify_{attempt}.png")
        except OSError:
            pass
        pixels = image.load()

        # Compare in PIXEL space against the solve-time grid. Re-parsing into
        # grid indices is NOT safe here: placed icons knock cells out of the
        # parse, whole columns disappear, and the fresh grid's index space
        # shifts relative to the plan - index-based repairs then drop aspects
        # onto physically different cells.
        try:
            board = find_frame(image, (150, 123, 123))
        except Exception:
            log.exception("Could not find the board frame to verify placement; skipping verification")
            return
        found_aspects = find_aspects_in_frame(board, pixels, image=image)
        empty_dots = find_squares_in_frame(board, pixels, (195, 195, 195), image=image)

        def at_pixel(px, py):
            for (bx0, by0, bx1, by1), name in found_aspects:
                if bx0 - 6 <= px <= bx1 + 6 and by0 - 6 <= py <= by1 + 6:
                    return name
            for ex, ey in empty_dots:
                if abs(ex - px) < 26 and abs(ey - py) < 26:
                    return "Free"
            return "Missing"

        # Free = definitely empty (the drag missed) -> re-drag those.
        # Missing = the cell couldn't be read at all: placed icons routinely
        # defeat the parser, so these have almost always landed fine -
        # re-dragging them just replays the whole placement and looks like
        # the bot running again. Report them, don't touch them.
        empty, unreadable, mismatched = [], [], []
        for coord, aspect in planned.items():
            px, py = state.solved.get_pixel_location(coord)
            actual = at_pixel(px, py)
            if actual == aspect:
                continue
            if actual == "Free":
                empty.append((coord, aspect))
            elif actual == "Missing":
                unreadable.append((coord, aspect))
            else:
                mismatched.append((coord, aspect, actual))

        for coord, aspect, actual in mismatched:
            log.warning("Cell %s,%s: planned %s but found %s", coord[0], coord[1], aspect, actual)

        if not empty:
            confirmed = len(planned) - len(unreadable) - len(mismatched)
            if (len(unreadable) + len(mismatched)) * 2 >= len(planned):
                # The completed-research signature: the game fades placed
                # aspects to ghosts and removes the hex grid, so most cells
                # read as unreadable (and faded colors can land on another
                # aspect's value, showing up as mismatches). A failed board
                # stays crisp and readable.
                log.info(
                    "Board reads as faded - the research most likely COMPLETED. "
                    "(%d/%d cells unreadable/mismatched is expected on a finished board; debug_verify_%d.png)",
                    len(unreadable) + len(mismatched), len(planned), attempt,
                )
            elif unreadable or mismatched:
                log.info(
                    "Placement check: %d/%d confirmed, %d unreadable, %d mismatched. debug_verify_%d.png shows the board.",
                    confirmed, len(planned), len(unreadable), len(mismatched), attempt,
                )
            else:
                log.info("Placement verified: all %d aspects landed", len(planned))
            return

        if attempt == attempts:
            # Include where each failing aspect was dragged from: a persistent
            # failure for one aspect usually means its inventory-panel slot was
            # misidentified, and the source box shows it directly.
            sources = []
            for c, a in empty:
                box = next((loc for loc, name in state.inventory_aspects if name == a), None)
                sources.append(f"{a}@{c[0]},{c[1]} (panel source: {box})")
            log.warning(
                "Placement incomplete after %d repair attempts - %d cells still empty: %s (plus %d unreadable, %d mismatched; see debug_verify_%d.png)",
                attempts, len(empty), "; ".join(sources), len(unreadable), len(mismatched), attempt,
            )
            return

        log.info(
            "Re-dragging %d aspect(s) whose cells are still empty: %s",
            len(empty),
            ", ".join(f"{a}@{c[0]},{c[1]}" for c, a in empty),
        )
        for coord, aspect in empty:
            # Always drag to the solve-time grid's pixel position - it is the
            # coordinate space the plan lives in.
            place_aspect_at(window_base_coords, state.inventory_aspects, state.solved, aspect, coord)


def console_mode(config: Config):
    state = BotState()
    while True:
        solve_board(config, state)

        action = "retry"
        while action == "retry":
            place_solution(state)
            action = wait_for_action(config.next_board_hotkey)


def gui_mode(config: Config):
    from .gui import run_gui

    state = BotState()

    def solve_and_place():
        solve_board(config, state)
        place_solution(state)

    def retry_place():
        place_solution(state)

    run_gui(solve_and_place, retry_place, hotkey=config.next_board_hotkey)


def wait_for_action(hotkey: str | None) -> str:
    """
    Waits for user input via Console (Enter='next', 'r'='retry') or Global Hotkey ('next').
    """
    print(f"-- Press Enter to process next board (or 'r' to retry, or global '{hotkey}') --")
    
    # Flush existing input
    while msvcrt.kbhit():
        msvcrt.getch()

    while True:
        # Check if Global Hotkey is pressed
        if hotkey is not None and keyboard.is_pressed(hotkey):
            print(f"Global hotkey '{hotkey}' detected.")
            return "next"

        # Check if Console Input was given
        if msvcrt.kbhit():
            # getch returns bytes (e.g., b'r'). It does not echo to screen.
            key = msvcrt.getch().lower()
            
            if key == b'r':
                print("Retrying aspect placing.")
                return "retry"
            elif key == b'\r': # Enter key
                print()
                return "next"
        
        sleep(0.05)


def test_all_samples(config: Config):
    test_files = list(Path("./test_inputs").glob("board_*.png"))
    print(f"Found {len(test_files)} test samples to check")

    for test_file in test_files:
        print("Testing file", test_file)
        image = PIL.Image.open(test_file)

        try:
            start_time = time.time()
            pixels = image.load()
            grid = generate_hexgrid_from_image(image, pixels)
            end_time = time.time()
        except Exception as e:
            print("Failed to parse:", traceback.format_exc())
            continue

        parse_time_ms = (end_time - start_time) * 1000

        try:
            start_time = time.time()
            solved = generate_solution_from_hexgrid(grid)
            end_time = time.time()
        except Exception as e:
            print("Failed to solve:", traceback.format_exc())
            continue

        solve_time_ms = (end_time - start_time) * 1000
        print(
            f"Solved with score {solved.calculate_cost()} in {parse_time_ms:.2f}+{solve_time_ms:.2f}ms"
        )


def setup_image(test_mode=True, skip_focus=False):
    if test_mode:
        image = PIL.Image.open("debug_input.png")
        window_base_coords = (0, 0)
    else:
        window = find_game(get_global_config().game_window_title)

        if not window.isActive:
            try:
                window.activate()
            except Exception as e:
                # pygetwindow raises "Error code from Windows: 0 - The
                # operation completed successfully" when the window is already
                # on its way to the foreground; harmless, keep going.
                log.warning("Window activate quirk ignored: %s", e)
            sleep(0.5)
        if not skip_focus:
            if not window.isMaximized:
                window.moveTo(10, 10)
                sleep(0.5)
                window.maximize()
                sleep(0.5)

        image, window_base_coords = screenshot_window(window)
        image.save("debug_input.png")

    return image, window_base_coords


def analyze_image_board(image: PIL.Image.Image, pixels):
    pixels = image.load()

    board = find_frame(image, (150, 123, 123))

    board_aspects = find_aspects_in_frame(board, pixels, image=image)
    log.debug("Aspects on board: %s", board_aspects)

    empty_hexagons = find_squares_in_frame(board, pixels, (195, 195, 195), image=image)
    log.debug("Empty spaces on board: %s", empty_hexagons)

    return board_aspects, empty_hexagons


def analyze_image_inventory(image: PIL.Image.Image, pixels):
    frame_aspects_left = find_frame(image, (100, 123, 123))
    frame_aspects_right = find_frame(image, (200, 123, 123))

    start_time = time.time()
    inventory_aspects = find_aspects_in_frame(
        frame_aspects_left, pixels, image=image
    ) + find_aspects_in_frame(frame_aspects_right, pixels, image=image)
    end_time = time.time()

    log.info(f"Time taken to find inventory aspects: {end_time - start_time} seconds")
    log.debug("Aspects in inventory: %s", inventory_aspects)

    return inventory_aspects


def group_hexagons(empty_hexagons, board_aspects, image_height):
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
        # Sort rows by y
        coords.sort(key=lambda c: c[1])

        # A placed aspect is drawn smaller than the hex, so part of the
        # empty-hex ring stays visible and the same cell can be detected as
        # both "Free" and an aspect a pixel apart (breaks post-placement
        # re-parsing). Merge near-duplicates, the aspect wins.
        deduped = []
        for entry in coords:
            if deduped and abs(entry[1] - deduped[-1][1]) < 10:
                if deduped[-1][2] == "Free" and entry[2] != "Free":
                    deduped[-1] = entry
                continue
            deduped.append(entry)
        coords = deduped

        if len(coords) == 1:
            column = [coords[0]]
            columns.append(column)
            continue

        difference_y = min(
            abs(coords[i + 1][1] - coords[i][1]) for i in range(len(coords) - 1)
        )
        if smallest_y_diff > difference_y:
            smallest_y_diff = difference_y

        if difference_y < 10:
            raise Exception(
                "Bad diff y, board is probably not clean:", difference_y, coords
            )

        column = [coords[0]]
        for i in range(len(coords) - 1):
            curr_diff = coords[i + 1][1] - column[-1][1]
            log.debug("Curr diff is %s vs expected %s", curr_diff, difference_y)
            while curr_diff > 1.5 * difference_y:
                column.append((coords[i][0], coords[i][1] + difference_y, "Missing"))
                curr_diff -= difference_y
            column.append(coords[i + 1])
        log.debug("Generated board column: %s", column)
        columns.append(column)

    log.debug("Smallest y diff between parsed board hexagons is %s", smallest_y_diff)
    valid_y_coords = []

    for col in columns:
        for row_entry in col:
            x, y, value = row_entry
            if any(
                abs(entry - y) < max(smallest_y_diff / 4, 5) for entry in valid_y_coords
            ):
                continue
            valid_y_coords.append(y)

    valid_y_coords.sort()

    # Patch holes in valid y coords
    for i in range(len(valid_y_coords) - 1):
        if valid_y_coords[i + 1] - valid_y_coords[i] > 0.75 * smallest_y_diff:
            log.debug("Fixing Y-hole between", valid_y_coords[i], valid_y_coords[i + 1])
            valid_y_coords.append(valid_y_coords[i] + smallest_y_diff * 0.5)

    valid_y_coords.sort()
    log.debug("Valid Y coords: %s", valid_y_coords)

    return columns, valid_y_coords, smallest_y_diff


def build_grid(columns, valid_y_coords, grid: HexGrid, smallest_y_diff):
    for x_index, col in enumerate(columns):
        for hex in col:
            x, y, value = hex
            y_index = -1
            for index, curr_y in enumerate(valid_y_coords):
                if abs(curr_y - y) < max(smallest_y_diff / 4, 5):
                    y_index = index
                    break
            if y_index == -1:
                raise Exception("Y value failure", y, valid_y_coords)

            grid.set_hex((x_index, y_index), value, (x, y))

def find_and_create_inventory_aspects(
    image: Image, pixels, window_base_coords: tuple[int, int]
) -> Tuple[list[OnscreenAspect], bool]:
    inventory_aspects = analyze_image_inventory(image, pixels)

    owned_aspects = set(name for _, name in inventory_aspects)
    all_aspects = set(aspect_parents.keys())

    missing_aspects = all_aspects - owned_aspects

    if MODE == "console":
        for aspect in missing_aspects:
            parent_a, parent_b = aspect_parents[aspect]
            log.error(
                f"Missing aspect {aspect} from inventory (made from {parent_a} + {parent_b})"
            )

    if len(missing_aspects) > 0:
        if MODE != "console":
            # GUI mode doesn't need the whole aspect catalog up front:
            # place_solution crafts exactly what each solution needs, with
            # quantities, right before placing.
            log.info(
                "Inventory is missing %d aspect kind(s) (will craft on demand): %s",
                len(missing_aspects), ", ".join(sorted(missing_aspects)),
            )
            return inventory_aspects, False
        text = input("Missing aspects from inventory! Should they be crafted automatically? [y/N]:")
        if text.lower() == "y":
            needs_next_iteration = True
            while needs_next_iteration:
                crafts = craft_missing_inventory_aspects(window_base_coords, inventory_aspects, missing_aspects)
                needs_next_iteration = crafts > 0

                sleep(0.05)
                # TODO: scuffed!
                image, window_base_coords = setup_image(
                    False
                )
                inventory_aspects = analyze_image_inventory(image, image.load())

                owned_aspects = set(name for _, name in inventory_aspects)
                print("Owned aspects:", owned_aspects)

                missing_aspects = all_aspects - owned_aspects
            if len(missing_aspects) > 0:
                print("Missing:", missing_aspects)
                raise Exception("Missing aspects from inventory even after crafting.")
            return inventory_aspects, True
        else:
            raise Exception("Missing aspects from inventory... safety shutdown")
    
    return inventory_aspects, False

def generate_hexgrid_from_image(image: Image, pixels) -> HexGrid:
    board_aspects, empty_hexagons = analyze_image_board(
        image, pixels
    )
    columns, valid_y_coords, smallest_y_diff = group_hexagons(
        empty_hexagons, board_aspects, image.height
    )

    grid = HexGrid()
    build_grid(columns, valid_y_coords, grid, smallest_y_diff)
    log.debug("Grid: %s", grid.grid)

    return grid


def generate_solution_from_hexgrid(grid: HexGrid) -> SolvingHexGrid:
    start_aspects: list[Tuple[int, int]] = []
    for (grid_x, grid_y), aspect in grid:
        if aspect != "Free" and aspect != "Missing":
            start_aspects.append((grid_x, grid_y))

    log.debug("Starting solve computation")
    start_time = time.time()
    solved = ringsolver_solve(grid, start_aspects)
    end_time = time.time()

    log.info(f"Time taken to compute solution: {end_time - start_time} seconds")
    log.info("Total solution cost: %s", solved.calculate_cost())


    # Check for duplicate coordinates in solution paths
    seen_coords = set()
    seen_coords_to_aspect = {}
    duplicate_coords = []
    for path_idx, path in enumerate(solved.applied_paths):
        for node_idx, (aspect, coord) in enumerate(path):
            if coord in seen_coords and seen_coords_to_aspect[coord] != aspect:
                duplicate_coords.append((coord, aspect))
                log.error(f"Duplicate coordinate {coord} found in solution! Path {path_idx}, node {node_idx}, aspect {aspect} was {seen_coords_to_aspect[coord]}")
            seen_coords.add(coord)
            seen_coords_to_aspect[coord] = aspect

                
    # If the solution is invalid, dont throw so a debug image is still generated
    if duplicate_coords:
        log.error("Invalid solution detected! Some hexes have multiple aspects assigned!")

    return solved


def save_input_image(image: Image, grid: HexGrid):
    board_hash = grid.hash_board()[:6]
    log.info("Saving sample image, Board hash is %s", board_hash)
    img_path = Path("./test_inputs/board_" + board_hash + ".png")
    try:
        if not img_path.exists():
            img_path.parent.mkdir(exist_ok=True)
            image.save(str(img_path))
    except OSError as e:
        # Saving the sample is best-effort; a read-only working dir (e.g. the
        # exe sitting in Program Files) must not break solving.
        log.warning("Could not save sample image: %s", e)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        # Keep the console open on a crash: double-click users otherwise see the
        # window flash and vanish with the error unread.
        traceback.print_exc()
        print()
        print("The bot crashed - the error is above.")
        print("Most common cause: the game isn't ready. Start Minecraft, open a")
        print("Research Table with an unsolved Research Notes item, then run this")
        print("again. The game window title must start with the game-window-title")
        print('value in config.toml (default: "GT: New Horizons").')
        input("Press Enter to close...")