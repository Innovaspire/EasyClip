"""8n+1 segment length helpers (inclusive frame count)."""


def is_valid_8n1(length_frames: int) -> bool:
    return length_frames > 0 and (length_frames - 1) % 8 == 0


def ceil_8n1_length(length_frames: int) -> int:
    """Smallest L' >= length such that (L'-1) % 8 == 0."""
    if length_frames <= 0:
        return 1
    if is_valid_8n1(length_frames):
        return length_frames
    # L' = 1 + 8 * ceil((length-1)/8)
    import math

    n = math.ceil((length_frames - 1) / 8)
    return 1 + 8 * n


def floor_8n1_length(length_frames: int) -> int:
    """Largest L' <= length such that (L'-1) % 8 == 0."""
    if length_frames <= 0:
        return 1
    if is_valid_8n1(length_frames):
        return length_frames
    import math

    n = math.floor((length_frames - 1) / 8)
    return max(1, 1 + 8 * n)


def snap_end_to_8n1_from_playhead(start_frame: int, playhead_frame: int, ceil: bool) -> int:
    """Adjust end (>= playhead for ceil semantics on length from start to playhead)."""
    L = playhead_frame - start_frame + 1
    if ceil:
        L2 = ceil_8n1_length(L)
    else:
        L2 = floor_8n1_length(L)
    return start_frame + L2 - 1
