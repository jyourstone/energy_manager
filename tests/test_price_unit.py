"""Tests for dynamic price-unit derivation (NOK/DKK/EUR support).

Pure tests for nordpool_adapter.derive_price_unit() and the
entity.get_price_unit() fallback chain -- EM's math is currency-agnostic,
so currency support is exactly "propagate the Nordpool sensor's unit".
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.energy_manager.entity import get_price_unit
from custom_components.energy_manager.nordpool_adapter import (
    DEFAULT_PRICE_UNIT,
    derive_price_unit,
)


class TestDerivePriceUnit:
    def test_unit_of_measurement_wins(self) -> None:
        """A per-kWh unit on the sensor is used verbatim."""
        assert derive_price_unit({"unit_of_measurement": "EUR/kWh"}) == "EUR/kWh"
        assert derive_price_unit({"unit_of_measurement": "NOK/kWh"}) == "NOK/kWh"

    def test_non_kwh_unit_falls_back_to_currency(self) -> None:
        """A non-per-kWh unit is ignored in favor of the currency attribute."""
        attrs = {"unit_of_measurement": "öre", "currency": "DKK"}
        assert derive_price_unit(attrs) == "DKK/kWh"

    def test_currency_attribute_alone(self) -> None:
        """The HACS integration's currency attribute builds the unit."""
        assert derive_price_unit({"currency": "EUR"}) == "EUR/kWh"

    def test_defaults_to_sek(self) -> None:
        """No usable attributes -> SEK/kWh, keeping existing installs stable."""
        assert derive_price_unit({}) == DEFAULT_PRICE_UNIT
        assert derive_price_unit({"currency": ""}) == DEFAULT_PRICE_UNIT
        assert derive_price_unit({"unit_of_measurement": None}) == DEFAULT_PRICE_UNIT


class TestGetPriceUnit:
    def _entry(self, price_unit: str | None) -> SimpleNamespace:
        data = None if price_unit is None else SimpleNamespace(price_unit=price_unit)
        return SimpleNamespace(
            runtime_data=SimpleNamespace(price_coordinator=SimpleNamespace(data=data))
        )

    def test_reads_price_coordinator_unit(self) -> None:
        assert get_price_unit(self._entry("EUR/kWh")) == "EUR/kWh"

    def test_falls_back_before_first_refresh(self) -> None:
        """No price data yet -> default unit instead of raising."""
        assert get_price_unit(self._entry(None)) == DEFAULT_PRICE_UNIT

    def test_falls_back_without_runtime_data(self) -> None:
        """Defensive read: entry without runtime_data (early setup)."""
        assert get_price_unit(SimpleNamespace()) == DEFAULT_PRICE_UNIT
