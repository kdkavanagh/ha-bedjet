"""BedJet switch entity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import BedJetConfigEntry
from .entity import BedJetEntity
from .fan_ramp import FanRampController
from .pybedjet import BedJet


@dataclass(frozen=True, kw_only=True)
class BedJetSwitchEntityDescription(SwitchEntityDescription):
    """BedJet switch entity description."""

    toggle_fn: Callable[[BedJet, bool], Any]
    value_fn: Callable[[BedJet], Any]


SWITCHES = (
    BedJetSwitchEntityDescription(
        key="enable_led",
        entity_category=EntityCategory.CONFIG,
        translation_key="enable_led",
        toggle_fn=lambda device, muted: device.set_led(muted),
        value_fn=lambda device: device.led_enabled,
    ),
    BedJetSwitchEntityDescription(
        key="mute_beeps",
        entity_category=EntityCategory.CONFIG,
        translation_key="mute_beeps",
        toggle_fn=lambda device, muted: device.set_muted(muted),
        value_fn=lambda device: device.beeps_muted,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BedJetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch platform for BedJet."""
    data = entry.runtime_data
    async_add_entities(
        [
            *(
                BedJetSwitchEntity(
                    data.coordinator, data.device, entry.title, descriptor
                )
                for descriptor in SWITCHES
            ),
            BedJetFanRampSwitch(
                data.coordinator, data.device, entry.title, data.ramp
            ),
        ]
    )


class BedJetSwitchEntity(BedJetEntity, SwitchEntity):
    """Representation of BedJet device."""

    entity_description: BedJetSwitchEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[None],
        device: BedJet,
        name: str,
        entity_description: BedJetSwitchEntityDescription,
    ) -> None:
        """Initialize a BedJet switch entity."""
        self.entity_description = entity_description
        self._attr_unique_id = f"{device.address}_{entity_description.key}"
        super().__init__(coordinator, device, name)

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_is_on = self.entity_description.value_fn(self._device)

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        await self.entity_description.toggle_fn(self._device, False)

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        await self.entity_description.toggle_fn(self._device, True)


class BedJetFanRampSwitch(BedJetEntity, SwitchEntity, RestoreEntity):
    """Switch enabling gradual fan-speed ramping. State persists across restarts."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "fan_ramp"

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[None],
        device: BedJet,
        name: str,
        ramp: FanRampController,
    ) -> None:
        """Initialize the fan-ramp switch."""
        self._ramp = ramp
        self._attr_unique_id = f"{device.address}_fan_ramp"
        super().__init__(coordinator, device, name)

    async def async_added_to_hass(self) -> None:
        """Restore the last enabled state and subscribe to ramp updates."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._ramp.set_enabled(last_state.state == STATE_ON)
        self.async_on_remove(
            self._ramp.add_listener(self._handle_coordinator_update)
        )

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_is_on = self._ramp.enabled

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable gradual fan ramping."""
        await self._ramp.async_set_enabled(False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable gradual fan ramping."""
        await self._ramp.async_set_enabled(True)
