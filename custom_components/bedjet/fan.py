"""BedJet fan entity."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import BedJetConfigEntry
from .entity import BedJetEntity
from .fan_ramp import FanRampController
from .pybedjet import BedJet, OperatingMode

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BedJetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the fan platform for BedJet."""
    data = entry.runtime_data
    async_add_entities(
        [BedJetFanEntity(data.coordinator, data.device, entry.title, data.ramp)]
    )


class BedJetFanEntity(BedJetEntity, FanEntity):
    """Representation of BedJet device."""

    _attr_name = None
    _attr_speed_count = 20
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.TURN_ON
    )

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[None],
        device: BedJet,
        name: str,
        ramp: FanRampController,
    ) -> None:
        """Initialize a BedJet fan entity."""
        self._ramp = ramp
        self._attr_unique_id = f"{device.address}_fan"
        super().__init__(coordinator, device, name)

    async def async_added_to_hass(self) -> None:
        """Subscribe to ramp updates so the displayed speed tracks the ramp."""
        self.async_on_remove(
            self._ramp.add_listener(self._handle_coordinator_update)
        )
        await super().async_added_to_hass()

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        device = self._device
        state = device.state
        is_on = state.operating_mode != OperatingMode.STANDBY
        self._attr_is_on = is_on
        # While ramping, show the requested target speed, not the live device speed.
        self._attr_percentage = (
            self._ramp.display_fan_speed(state.fan_speed) if is_on else 0
        )

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed of the fan, as a percentage."""
        if percentage == 0:
            return await self.async_turn_off()
        await self.async_turn_on(percentage=percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        self._ramp.cancel()
        await self._device.set_operating_mode(OperatingMode.STANDBY)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        if self._device.state.operating_mode == OperatingMode.STANDBY:
            await self._device.set_operating_mode(OperatingMode.COOL)
        if percentage:
            await self._ramp.request_fan_speed(percentage)
