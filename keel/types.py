"""Compatibility shim: `keel.types` now lives in `keel_core.types`.

Retained so this task's diff stays mechanical; call sites migrate in a later task.
"""

from keel_core.types import *  # noqa: F403
from keel_core.types import __all__  # noqa: F401
