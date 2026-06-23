"""
Test helpers for importing project modules with lightweight stub dependencies.
"""

from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from collections.abc import Mapping
from types import ModuleType
from typing import Any, cast


@contextmanager
def isolated_module_import(module_name: str, stub_modules: Mapping[str, object]):
    """Temporarily injects stub modules while importing the requested module."""
    original_target = sys.modules.get(module_name)
    original_stubs = {name: sys.modules.get(name) for name in stub_modules}

    try:
        sys.modules.pop(module_name, None)
        for name, module in stub_modules.items():
            sys.modules[name] = cast(ModuleType, module)
        imported = importlib.import_module(module_name)
        yield imported
    finally:
        sys.modules.pop(module_name, None)
        if original_target is not None:
            sys.modules[module_name] = original_target
        for name, module in original_stubs.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def set_module_attr(module: object, name: str, value: object) -> None:
    """Type-safe monkeypatch helper for imported module objects in tests."""
    setattr(cast(Any, module), name, value)
