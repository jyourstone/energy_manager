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

from custom_components.energy_manager.entity import _VIA_DEVICE_ID_SUPPORTED


def test_via_device_id_not_supported_under_test_stubs() -> None:
    """Under the MagicMock-based HA stubs, DeviceInfo has no real
    __optional_keys__, so getattr's fallback keeps the flag False --
    matching real behavior on HA < 2026.8.
    """
    assert _VIA_DEVICE_ID_SUPPORTED is False


def test_optional_keys_detection_true_when_via_device_id_present() -> None:
    """A DeviceInfo-shaped object exposing via_device_id in its
    __optional_keys__ (as on HA >= 2026.8) is detected as supported.
    """

    class _NewDeviceInfo:
        __optional_keys__ = frozenset({"via_device_id", "sw_version"})

    assert "via_device_id" in getattr(_NewDeviceInfo, "__optional_keys__", ())


def test_optional_keys_detection_false_when_via_device_id_absent() -> None:
    """A DeviceInfo-shaped object without via_device_id (as on HA < 2026.8,
    or an object with no __optional_keys__ at all) is detected as
    unsupported.
    """

    class _OldDeviceInfo:
        __optional_keys__ = frozenset({"via_device", "sw_version"})

    assert "via_device_id" not in getattr(_OldDeviceInfo, "__optional_keys__", ())
    assert "via_device_id" not in getattr(object(), "__optional_keys__", ())
