"""Read the duration of an uploaded .m4a without trusting the client."""

from mutagen import MutagenError
from mutagen.mp4 import MP4


def m4a_duration_sec(fileobj) -> float | None:
    """Duration in seconds, or None if this isn't a readable MP4/M4A.

    Leaves the file positioned at the start for whoever reads it next.
    """
    try:
        fileobj.seek(0)
        length = MP4(fileobj).info.length
    except (MutagenError, ValueError, OSError):
        return None
    finally:
        fileobj.seek(0)
    if not isinstance(length, (int, float)) or length <= 0:
        return None
    return float(length)
