"""Pure frame and wandering geometry, independent of Qt."""
import math
import random

def _squash_geometry(
    window_width: int,
    window_height: int,
    frame_width: int,
    frame_height: int,
    progress: float,
) -> tuple[int, int, int, int]:
    """返回 Q 弹帧的逻辑坐标，避免把 DPR 物理像素当成 QWidget 坐标。"""
    progress = max(0.0, min(1.0, float(progress)))
    pulse = math.sin(math.pi * progress)
    sy = 1.0 - 0.15 * pulse
    sx = 1.0 + 0.10 * pulse
    width = max(1, int(round(frame_width * sx)))
    height = max(1, int(round(frame_height * sy)))
    x = int(round((window_width - width) / 2))
    y = window_height - height
    return x, y, width, height



def wander_target_y(
    start_y: float,
    top: float,
    bottom: float,
    height: float,
    margin: float,
    rnd=random,
) -> int:
    """Pick a bounded vertical wander target; injectable RNG keeps it testable."""
    y_lo = top + margin
    y_hi = bottom - height - margin
    if y_hi <= y_lo:
        return int(start_y)
    max_dy = max(40, int((y_hi - y_lo) * 0.25))
    return int(max(y_lo, min(y_hi, start_y + rnd.randint(-max_dy, max_dy))))

