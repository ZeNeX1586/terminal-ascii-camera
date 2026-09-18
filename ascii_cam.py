#!/usr/bin/env python3
"""
Terminal ASCII Camera
=====================
Renders a live camera feed as colored ASCII art directly in your terminal.

Features
--------
- Auto-fit to terminal size, responds to window resize in real time (SIGWINCH).
- Correct aspect ratio (accounts for terminal character height / width).
- True color (24-bit) or 256-color palette modes.
- Horizontal mirror (selfie-style) by default.
- Graceful cleanup on Ctrl+C, SIGTERM, SIGHUP — camera is always released.

Dependencies
------------
    sudo apt install python3-opencv
    # or
    pip install opencv-python numpy

Usage
-----
    python3 ascii_cam.py           # open /dev/video0
    python3 ascii_cam.py 2         # open /dev/video2
"""

import sys
import signal
import shutil
import time
import atexit
import cv2
import numpy as np

# ── Configuration ────────────────────────────────────────────────────────────

CHARS = " .:-=+*#%@"     # dark → light
CHAR_ASPECT = 2.0        # terminal char height / width (tune for your font)
FPS_LIMIT = 30
MIRROR = True            # horizontal flip (selfie-view)
COLOR_MODE = "truecolor"  # "truecolor" (24-bit) or "256" (xterm-256 palette)

# ── xterm-256 palette LUTs (used only in "256" mode) ─────────────────────────

_CUBE_VALS = np.array([0, 95, 135, 175, 215, 255], dtype=np.int32)
_GRAY_VALS = np.arange(8, 8 + 24 * 10, 10, dtype=np.int32)

_CUBE_LUT = np.zeros(256, dtype=np.int32)
for _v in range(256):
    _CUBE_LUT[_v] = int(np.argmin(np.abs(_CUBE_VALS - _v)))

_GRAY_LUT = np.zeros(256, dtype=np.int32)
for _v in range(256):
    _GRAY_LUT[_v] = int(np.argmin(np.abs(_GRAY_VALS - _v)))

# ── Global state ─────────────────────────────────────────────────────────────

resize_pending = True
cap = None
cleanup_done = False

# ── Signal handlers and cleanup ──────────────────────────────────────────────

def handle_sigwinch(signum, frame):
    global resize_pending
    resize_pending = True

def handle_termination(signum, frame):
    raise KeyboardInterrupt

def cleanup():
    global cap, cleanup_done
    if cleanup_done:
        return
    cleanup_done = True
    if cap is not None:
        try:
            cap.release()
        except Exception:
            pass
        cap = None
    try:
        sys.stdout.write("\033[?25h\033[0m\033[?1049l\033[2J\033[H")
        sys.stdout.flush()
    except Exception:
        pass

# ── Terminal utilities ───────────────────────────────────────────────────────

def get_terminal_size():
    s = shutil.get_terminal_size(fallback=(80, 24))
    return s.columns, s.lines

# ── 256-color quantization ───────────────────────────────────────────────────

def rgb_to_256(rgb):
    """rgb: (H, W, 3) uint8 -> xterm-256 palette indices (H, W)."""
    r = rgb[..., 0].astype(np.int32)
    g = rgb[..., 1].astype(np.int32)
    b = rgb[..., 2].astype(np.int32)

    qr = _CUBE_LUT[r]
    qg = _CUBE_LUT[g]
    qb = _CUBE_LUT[b]
    cube_idx = 16 + 36 * qr + 6 * qg + qb
    cube_dist = (
        (r - _CUBE_VALS[qr]) ** 2
        + (g - _CUBE_VALS[qg]) ** 2
        + (b - _CUBE_VALS[qb]) ** 2
    )

    gray = (r + g + b) // 3
    gray_q = _GRAY_LUT[gray]
    gray_idx = 232 + gray_q
    gray_val = _GRAY_VALS[gray_q]
    gray_dist = (r - gray_val) ** 2 + (g - gray_val) ** 2 + (b - gray_val) ** 2

    return np.where(cube_dist <= gray_dist, cube_idx, gray_idx).astype(np.uint8)

# ── Frame rendering ──────────────────────────────────────────────────────────

def frame_to_ascii_rgb(frame, cols, rows):
    """Convert BGR frame to a list of ANSI-colored terminal lines."""
    rows = max(1, rows - 1)  # one row at the bottom left free to avoid scroll

    h, w = frame.shape[:2]

    # Virtual grid: horizontal = cols, vertical = rows * CHAR_ASPECT (square units)
    grid_w = cols
    grid_h = rows * CHAR_ASPECT
    scale = min(grid_w / w, grid_h / h)

    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale / CHAR_ASPECT))

    small = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

    bgr = small
    rgb = bgr[..., ::-1]

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    idx = (gray.astype(np.uint16) * (len(CHARS) - 1)) // 255

    if COLOR_MODE == "256":
        color_data = rgb_to_256(rgb)
    else:
        color_data = rgb

    # Center horizontally
    pad_left = (cols - new_w) // 2
    pad = " " * pad_left

    lines = []
    for y in range(new_h):
        row_idx = idx[y]
        row_color = color_data[y]
        parts = [pad]
        prev = None

        if COLOR_MODE == "256":
            for x in range(new_w):
                c = int(row_color[x])
                if c != prev:
                    parts.append("\033[38;5;%dm" % c)
                    prev = c
                parts.append(CHARS[int(row_idx[x])])
        else:
            for x in range(new_w):
                col = row_color[x]
                c = (int(col[0]), int(col[1]), int(col[2]))
                if c != prev:
                    parts.append("\033[38;2;%d;%d;%dm" % c)
                    prev = c
                parts.append(CHARS[int(row_idx[x])])

        parts.append("\033[0m")
        lines.append("".join(parts))

    return lines

# ── Main loop ────────────────────────────────────────────────────────────────

def main():
    global cap, resize_pending

    cam = 0
    if len(sys.argv) > 1:
        try:
            cam = int(sys.argv[1])
        except ValueError:
            print("Invalid camera index: " + sys.argv[1], file=sys.stderr)
            sys.exit(2)

    signal.signal(signal.SIGWINCH, handle_sigwinch)
    signal.signal(signal.SIGINT,   handle_termination)
    signal.signal(signal.SIGTERM,  handle_termination)
    try:
        signal.signal(signal.SIGHUP, handle_termination)
    except AttributeError:
        pass

    atexit.register(cleanup)

    cap = cv2.VideoCapture(cam)
    if not cap.isOpened():
        print("Cannot open /dev/video%d" % cam, file=sys.stderr)
        print("Try: sudo fuser -k /dev/video0", file=sys.stderr)
        sys.exit(1)

    # Enter alt screen, hide cursor, clear
    sys.stdout.write("\033[?1049h\033[?25l\033[2J")
    sys.stdout.flush()

    cols, rows = get_terminal_size()
    last = 0.0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue

            if MIRROR:
                frame = cv2.flip(frame, 1)

            if resize_pending:
                cols, rows = get_terminal_size()
                sys.stdout.write("\033[2J")
                resize_pending = False

            lines = frame_to_ascii_rgb(frame, cols, rows)
            buf = "\033[H" + "\n".join(lines) + "\033[0m"
            sys.stdout.write(buf)
            sys.stdout.flush()

            now = time.time()
            target = 1.0 / FPS_LIMIT
            el = now - last
            if el < target:
                time.sleep(target - el)
            last = time.time()

    except KeyboardInterrupt:
        pass
    finally:
        cleanup()

if __name__ == "__main__":
    main()
