"""Give source and packaged IRU windows the same Windows taskbar identity."""
from __future__ import annotations

import ctypes
import sys

APP_USER_MODEL_ID = "IRU.Agent.Desktop"


def set_taskbar_identity(logger=None) -> None:
    if sys.platform != "win32":
        return
    try:
        setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        result = setter(APP_USER_MODEL_ID)
        if result < 0:
            raise OSError(f"AppUserModelID HRESULT={result:#x}")
    except (AttributeError, OSError) as error:
        if logger is not None:
            logger.warning("[desktop] taskbar identity unavailable: %s", error)
