"""Compatibility shim: `keel.logging_setup` now lives in `keel_core.logging_setup`."""

from keel_core.logging_setup import *  # noqa: F403
from keel_core.logging_setup import __all__  # noqa: F401
