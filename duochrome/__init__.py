"""duoChrome — lightweight multi-profile browser isolation.

Open N independent Chromium instances, each with its own cookies / cache / storage.
Like AdsPower / BitBrowser for solo devs.
"""
from __future__ import annotations

from .core import DuoChrome, LaunchFailedError, ProfileAlreadyRunning
from .profile import Profile, ProfileStore

__version__ = "0.1.2"

__all__ = ["DuoChrome", "Profile", "ProfileStore", "ProfileAlreadyRunning", "LaunchFailedError"]