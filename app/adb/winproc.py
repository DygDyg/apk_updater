from __future__ import annotations

import os
import subprocess


def hidden_run_kwargs() -> dict:
    """
    kwargs for subprocess.run so console windows do not flash on Windows.
    Safe no-op on other platforms.
    """
    if os.name != "nt":
        return {}
    # Available since Python 3.7 on Windows
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return {"creationflags": flag}
