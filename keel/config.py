"""Compatibility shim: `keel.config` now lives in `keel_core.config`."""

from keel_core.config import *  # noqa: F403
from keel_core.config import __all__  # noqa: F401
