"""
Test helpers for importing project modules with lightweight stub dependencies.
"""

from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, cast

_PROJECT_MODULES = frozenset(
    path.stem for path in Path(__file__).resolve().parents[1].glob("*.py")
)


@contextmanager
def isolated_module_import(module_name: str, stub_modules: Mapping[str, object]):
    """Temporarily injects stub modules while importing the requested module."""
    # Project modules bind dependencies at import time. Reimport the whole local
    # graph so cached wrappers cannot retain a previous test's config or locks.
    managed = _PROJECT_MODULES | {module_name} | set(stub_modules)
    originals = {name: sys.modules[name] for name in managed if name in sys.modules}
    try:
        for name in managed:
            sys.modules.pop(name, None)
        for name, module in stub_modules.items():
            sys.modules[name] = cast(ModuleType, module)
        yield importlib.import_module(module_name)
    finally:
        for name in managed:
            sys.modules.pop(name, None)
        sys.modules.update(originals)


def set_module_attr(module: object, name: str, value: object) -> None:
    """Type-safe monkeypatch helper for imported module objects in tests."""
    setattr(cast(Any, module), name, value)
