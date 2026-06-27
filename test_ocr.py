"""Test the count OCR against a known sample board."""
import sys
import types
from pathlib import Path

# Stub config
cfg = types.ModuleType("src.utils.config")
class _C:
    disabled_aspects: list[str] = []
    aspect_cost_overrides: dict[str, int] = {}
    game_window_title = ""
    next_board_hotkey = None
cfg.get_global_config = lambda: _C()
cfg.Config = _C
sys.modules["src.utils.config"] = cfg

from PIL import Image
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.finder import find_frame, find_aspects_in_frame
from src.utils.count_ocr import ocr_aspect_cell


def main():
    board_path = sys.argv[1] if len(sys.argv) > 1 else "test_inputs/board_guObMF.png"
    image = Image.open(board_path)
    pixels = image.load()
    arr = np.array(image)

    for frame_color, side in [((100, 123, 123), "left"), ((200, 123, 123), "right")]:
        try:
            frame = find_frame(image, frame_color)
        except Exception as e:
            print(f"[{side}] frame detection failed: {e}")
            continue
        aspects = find_aspects_in_frame(frame, pixels)
        # find_aspects_in_frame falsely detects the white count digits as
        # "tempestas" (which is colour-coded pure white). Real aspect blocks
        # are ~60+ px on a side; digit blobs are tiny. Filter by size.
        real = [(b, n) for (b, n) in aspects
                if (b[2] - b[0]) > 30 and (b[3] - b[1]) > 30]
        print(f"\n=== {side} panel: {len(aspects)} detections, "
              f"{len(real)} real aspects ===")
        for bbox, name in real:
            count, conf = ocr_aspect_cell(arr, bbox)
            print(f"  {name:14s}  count={count}  conf={conf:.3f}  bbox={bbox}")


if __name__ == "__main__":
    main()
