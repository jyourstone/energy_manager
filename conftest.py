"""Root-level pytest configuration for Energy Manager.

Provides comprehensive stubs for the homeassistant package so pure-Python
modules can be imported without a full HA installation. This file is at the
project root so it is loaded by pytest before any test module collection.

Uses importlib.abc MetaPathFinder/Loader to stub any homeassistant.* import
with modules whose attributes return MagicMock objects on access.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types
from unittest.mock import MagicMock


class _StubModule(types.ModuleType):
    """A module stub that returns MagicMock for any undefined attribute."""

    def __getattr__(self, name: str):
        mock = MagicMock()
        mock.__class_getitem__ = classmethod(lambda cls, x: cls)
        setattr(self, name, mock)
        return mock


class _HAStubLoader(importlib.abc.Loader):
    """Loader that creates StubModule instances for homeassistant packages."""

    def create_module(self, spec):
        stub = _StubModule(spec.name)
        stub.__path__ = []
        stub.__package__ = spec.name
        return stub

    def exec_module(self, module):
        pass


class _HAStubFinder(importlib.abc.MetaPathFinder):
    """Meta-path finder that intercepts any homeassistant.* import."""

    _PREFIX = "homeassistant"
    _loader = _HAStubLoader()

    def find_spec(self, fullname, path, target=None):
        if fullname == self._PREFIX or fullname.startswith(self._PREFIX + "."):
            return importlib.machinery.ModuleSpec(
                fullname,
                self._loader,
                is_package=True,
            )
        return None


# Install the finder once, before any collection
if not any(isinstance(f, _HAStubFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _HAStubFinder())
