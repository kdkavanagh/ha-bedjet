"""BedJet binary sensor entity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import BedJetConfigEntry
from .entity import BedJetEntity
from .fan_ramp import FanRampController
from .pybedjet import BedJet


@dataclass(frozen=True, kw_only=True)
class BedJetBinarySensorEntityDescription(BinarySensorEntityDescription):
    """BedJet binary sensor entity description."""

    value_fn: Callable[[BedJet], Any]


SENSORS = (
    BedJetBinarySensorEntityDescription(
        key="connection_test",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda device: device.connection_test_passed,
    ),
    BedJetBinarySensorEntityDescription(
        key="dual_zone",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        translation_key="dual_zone",
        value_fn=lambda device: device.dual_zone,
    ),
    BedJetBinarySensorEntityDescription(
        key="units_setup",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        translation_key="units_setup",
        value_fn=lambda device: device.units_setup,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BedJetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor platform for BedJet."""
    data = entry.runtime_data
    async_add_entities(
        [
            *(
                BedJetBinarySensorEntity(
                    data.coordinator, data.device, entry.title, descriptor
                )
                for descriptor in SENSORS
            ),
            BedJetFanRampingBinarySensor(
                data.coordinator, data.device, entry.title, data.ramp
            ),
        ]
    )


class BedJetBinarySensorEntity(BedJetEntity, BinarySensorEntity):
    """Representation of BedJet device."""

    entity_description: BedJetBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[None],
        device: BedJet,
        name: str,
        entity_description: BedJetBinarySensorEntityDescription,
    ) -> None:
        """Initialize a BedJet binary sensor entity."""
        self.entity_description = entity_description
        self._attr_unique_id = f"{device.address}_{entity_description.key}"
        super().__init__(coordinator, device, name)

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_is_on = self.entity_description.value_fn(self._device)


class BedJetFanRampingBinarySensor(BedJetEntity, BinarySensorEntity):
    """Diagnostic sensor reporting whether a fan-speed ramp is in progress."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "fan_ramping"

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[None],
        device: BedJet,
        name: str,
        ramp: FanRampController,
    ) -> None:
        """Initialize the fan-ramping binary sensor."""
        self._ramp = ramp
        self._attr_unique_id = f"{device.address}_fan_ramping"
        super().__init__(coordinator, device, name)

    async def async_added_to_hass(self) -> None:
        """Subscribe to ramp state updates."""
        self.async_on_remove(
            self._ramp.add_listener(self._handle_coordinator_update)
        )
        await super().async_added_to_hass()

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_is_on = self._ramp.is_ramping
