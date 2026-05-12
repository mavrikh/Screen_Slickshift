# Cursor fix feature flags.
#
# Each key controls one independent cursor bug fix. Set a flag to False and
# restart the app to disable that specific fix without reverting any commits.
# All fixes default to True (enabled).
#
# fix_return_dwell   — Bug 4: arm the return edge detector with 600 ms dwell
#                      instead of 0, preventing an instant return trigger on
#                      cursor handoff.
# fix_capture_anchor — Bug 3: use warp_cursor_to_center() (DPI-aware) to
#                      establish the capture loop anchor instead of a raw
#                      pyautogui.moveTo(sw//2, sh//2) which uses physical
#                      coords on Mac Retina while position() returns logical.
# fix_windows_dpi    — Bug 2: call SetProcessDpiAwareness on Windows so
#                      pyautogui coord reads and writes are in the same space.
# fix_movement_scale — Bug 1: fetch the remote machine's screen dimensions and
#                      scale dx/dy by the ratio of remote/local resolution so
#                      movement feels 1:1 across different-resolution screens.

CURSOR_FIX_FLAGS = {
    "fix_return_dwell":   True,   # Bug 4
    "fix_capture_anchor": True,   # Bug 3
    "fix_windows_dpi":    True,   # Bug 2
    "fix_movement_scale": True,   # Bug 1
}
