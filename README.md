# Thaumcraft 4 Research Bot

> **Fork notice.** This is a fork of [**leumasme/thaumcraft4-research-bot**](https://github.com/leumasme/thaumcraft4-research-bot)
> by Temm — the original author of the bot, solver, and pixel-recognition pipeline.
> This fork adds **OCR-based inventory-count detection** and **scarcity-aware cost
> weighting** (see [This fork's additions](#this-forks-additions)). Licensed under
> GPL-3.0, same as upstream; all original copyright and history are preserved.

This is a screenshot-based bot to automate the Thaumcraft 4 research minigame (Minecraft 1.7.10).  
Made for the [Gregtech: New Horizons](https://github.com/GTNewHorizons/GT-New-Horizons-Modpack) Modpack (uses TC Research Tweaks addon)  
- Let me know if there's any other decent 1.7.10 modpacks using Thaumcraft 4, adding support shouldn't be much work

Meant to replace the various Thaumcraft Research "Helper" websites

## Preview

https://github.com/user-attachments/assets/235ce89c-b1fc-477e-9aa5-c23455fcd1ae

## Features
- Pixel-based puzzle recognition
  - Custom Resource Pack required
- Fast, Efficient universal puzzle solver
  - Tested to work on all research puzzles in GTNH
  - Generates solutions that use simple aspects
  - Optimized for speed (for a python project...)
- Automatic mouse control to quickly input found puzzle solutions
- Automatic mouse control to craft undiscovered aspects
  - Experimental

## Usage

Some technical know-how currently required.
- Install [uv](https://docs.astral.sh/uv/) (python runner / package manager)
- Download the code of the project and unzip it into a new folder
- Prepare the Game
  - Install and activate the required resource pack
  - Open a Research Table and put in an unsolved Research Notes item
    - There shouldn't be any aspects on the puzzle board except the initially given ones
  - Make sure the game window is on your main screen and is large enough
  - Make sure a large item tooltip isn't covering up the game board
    - You may want to hide NEI (default keybind in GTNH: `O`)
- Open a terminal in the project folder (Windows Terminal/Powershell/CMD)
- Start the project with the command: `uv run -m src`
  - A persistent window opens. Press **Solve board** (or the global hotkey,
    default `ctrl+r`) and the bot will:
    - Bring the game to the foreground
    - Take a screenshot
    - Parse the puzzle board from the screenshot
    - Generate a solution for the puzzle
    - Move the mouse to place the aspects according to the solution
- After it's done placing aspects, put in the next unsolved Research Notes and
  press Solve again — the window stays open between boards, shows the bot's
  log, and has a **Retry placement** button if a placement got interrupted
- Prefer the old terminal-only flow? Run `uv run -m src console`

## Limitations
- **No Linux Support**
  - I don't want to deal with finding a universal way of taking screenshots and performing mouse input
- Solver algorithm currently doesn't scale well with many (7+) given aspects on large boards
  - It gets quite slow. On the largest boards (9+ given aspects) it may currently take *minutes* to calculate
- ~~No detection for how many Aspects the player owns~~ — **solved in this fork**
  (inventory-count OCR + scarcity-aware costs, see
  [This fork's additions](#this-forks-additions))
- Currently no way to reduce the mouse interaction speed
  - The current speed works consistently for me, but might break on laggier machines.
- Not well tested on different GUI sizes & Screen Resolutions
- Currently not very user-friendly. Missing:
  - A no-code way to configure custom costs for aspects
  - ~~Pre-bundled .exe releases~~ — **available on
    [this fork's releases page](https://github.com/philipxjm/thaumcraft4-research-bot/releases)**
  - More comprehensible error messages

## FAQ

Q: The mouse control is going wild! How do I stop it?  
A: Smash your mouse into the top-left corner of the screen for an emergency stop

Q: Why not just pre-compute the best solution for all puzzles?  
A: The "holes" in the puzzle board are randomly placed when creating the Research Notes, so puzzles aren't always the same.  

Q: Why Python?  
A: It's the only language I know where I could find decent libraries to take screenshots and perform mouse input.  
Even these aren't good, though: the screenshot library just fails when the Window is at negative screen coordinates?

Q: Why isn't this a mod?  
A: That's too cheaty in my opinion.  

## Building & Running

To run as a non-technical user (Windows):
1. Download `thaumcraft4-research-bot-windows.zip` **and** `resource-pack.zip` from
   [this fork's releases page](https://github.com/philipxjm/thaumcraft4-research-bot/releases).
2. Extract `thaumcraft4-research-bot-windows.zip` anywhere and run
   `thaumcraft4-research-bot.exe` from inside the extracted folder (keep the folder
   contents together — the `.exe` needs the files next to it). On first run it
   auto-creates a `config.toml` with sensible defaults.
3. Drop `resource-pack.zip` into your `.minecraft/resourcepacks/` folder and
   enable it in-game (Options → Resource Packs).

> The build is an unsigned Python executable, so Windows Defender may show a
> heuristic warning (e.g. `Wacatac.B!ml`) — this is a known false positive for
> compiled Python apps. The full source + the GitHub Actions build log are public
> if you'd like to verify; you can also just run from source (below).

(The original upstream build is on [leumasme's releases page](https://github.com/leumasme/thaumcraft4-research-bot/releases);
this fork additionally includes the inventory-count OCR feature.)

To run:
- Install [uv](https://docs.astral.sh/uv/)
- Run in CLI: `uv run -m src`
  - To run in test mode: `uv run -m src test`
    - This uses `debug_input.png` instead of taking a screenshot, and doesn't perform any clicks/window actions
  - To run in test-all mode: `uv run -m src test_all`
    - Runs test mode for all inputs in the `test_inputs` folder, for benchmarking & testing that all boards can be solved
    - This repo ships only two sample boards to keep the download small; the full
      ~700-board corpus is in the
      [upstream repository](https://github.com/leumasme/thaumcraft4-research-bot)'s
      `test_inputs` folder

To build into a .exe (standalone folder; don't use `--mode=onefile`, its
self-extracting bootloader is a common antivirus false positive):
`uv run -m nuitka --python-flag=-m --output-dir=dist --mode=standalone --lto=yes --include-data-dir=resources=resources src`

## Issues, Contributions, Contact

Use Github.  
When encountering a crash on a specific puzzle, include the generated "debug_input.png".  
You may also find me on the GTNH discord server.

## This fork's additions

Upstream listed *"No detection for how many Aspects the player owns"* as a
limitation. This fork removes it:

- **Inventory-count OCR** (`src/utils/count_ocr.py`) — template-matches the
  Minecraft default-font digits Minecraft draws over each aspect slot to read
  how many of each aspect you own, straight from the same screenshot.
- **Scarcity-aware cost weighting** (`update_costs_from_inventory` in
  `src/utils/aspects.py`) — re-weights the solver's aspect costs each board so
  scarce aspects cost more (`ceil(scarcity_weight / count)`); compound aspects
  are recomputed from their parents. Explicit `aspect_cost_overrides` in
  `config.toml` still win. Wired into the main loop with a graceful fallback to
  default costs if OCR fails.
- **Cross-platform solver tests** — `test_ocr.py`, `test_e2e.py`, `repro.py`,
  and `regression.py` stub out the Windows-only input deps so the OCR + solver
  can be exercised on any OS (`uv run python test_e2e.py`). Sample boards live
  in `test_inputs/`.

Credit for everything else goes to the original author. Bug reports for the
OCR/cost-weighting additions specifically can go to this fork's issue tracker.
