"""Tests for the via_device -> via_device_id migration feature detection.

HA deprecated DeviceInfo["via_device"] in 2026.8 in favor of via_device_id
(removal planned for 2027.8). entity.py gates on via_device_id support with
a module-level flag computed from DeviceInfo.__optional_keys__.

CarEntity/ApplianceEntity themselves can't be instantiated here: this test
suite's homeassistant stubs (root conftest.py) replace
homeassistant.helpers.update_coordinator.CoordinatorEntity with a MagicMock
instance, and subclassing a MagicMock instance makes Python's class-creation
machinery produce another MagicMock instead of a real class -- a
pre-existing limitation of the stub harness, unrelated to this change. So
this module exercises the real, importable piece: the feature-detection
flag and the getattr(..., "__optional_keys__", ()) pattern it relies on.
"""

from __future__ import annotations

from custom_components.energy_manager.entity import (
    _VIA_DEVICE_ID_SUPPORTED,
    _supports_via_device_id,
)


def test_via_device_id_not_supported_under_test_stubs() -> None:
    """Under the MagicMock-based HA stubs, DeviceInfo has no real
    __optional_keys__, so getattr's fallback keeps the flag False --
    matching real behavior on HA < 2026.8.
    """
    assert _VIA_DEVICE_ID_SUPPORTED is False


def test_supports_via_device_id_true_when_key_present() -> None:
    """A DeviceInfo-shaped class exposing via_device_id in its
    __optional_keys__ (as on HA >= 2026.8) is detected as supported.
    """

    class _NewDeviceInfo:
        __optional_keys__ = frozenset({"via_device_id", "sw_version"})

    assert _supports_via_device_id(_NewDeviceInfo) is True


def test_supports_via_device_id_false_when_key_absent() -> None:
    """A DeviceInfo-shaped class without via_device_id (as on HA < 2026.8,
    or a class with no __optional_keys__ at all) is detected as
    unsupported.
    """

    class _OldDeviceInfo:
        __optional_keys__ = frozenset({"via_device", "sw_version"})

    assert _supports_via_device_id(_OldDeviceInfo) is False
    assert _supports_via_device_id(object) is False
