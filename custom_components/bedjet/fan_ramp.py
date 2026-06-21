"""Controlled fan-speed ramping for BedJet.

When the fan speed is *increased*, the BedJet would normally jump straight to
the new speed. At low airflow the delivered air is hotter, so the bed warms up
faster; ramping the fan up gradually as the air temperature catches up keeps the
target temperature maintained instead of dumping cold-ish high airflow at once.

This controller is shared by the climate and fan entities (both drive fan speed)
so a single ramp governs the device regardless of which entity is commanded. It
is enabled/disabled by a switch entity, and exposes ``is_ramping`` for a
diagnostic binary sensor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .pybedjet import BedJet, OperatingMode

_LOGGER = logging.getLogger(__name__)

RAMP_STEP = 5  # percent added per increment
RAMP_STEP_DELAY = 4.0  # seconds to wait after each increment
WARM_ON_FLOOR = 10  # fan speed to drop to when warming up from standby
TEMP_CATCHUP_TIMEOUT = 120.0  # cap on waiting for current temp to reach target

# Only these modes actually produce heat, so only here does waiting for the
# current (outlet) temperature to reach the target make sense. DRY is "high fan,
# no heat" and COOL is fan-only - gating on temperature there would just stall.
HEATING_MODES = (
    OperatingMode.HEAT,
    OperatingMode.EXTENDED_HEAT,
    OperatingMode.TURBO,
)


class FanRampController:
    """Ramp the BedJet fan speed up gradually so the target temp is maintained."""

    def __init__(self, device: BedJet) -> None:
        """Init the controller."""
        self._device = device
        self._enabled = True
        self._target_fan_speed: int | None = None
        self._task: asyncio.Task | None = None
        self._listeners: list[Callable[[], None]] = []

    # --- listener plumbing (entities subscribe for snappy state updates) ---

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a listener; returns an unsubscribe callable."""
        self._listeners.append(callback)

        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _remove

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    # --- enable flag (driven by the switch entity) ---

    @property
    def enabled(self) -> bool:
        """Return whether gradual ramping is enabled."""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable ramping. Disabling cancels any ramp in progress."""
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self.cancel()
        self._notify()

    # --- ramping state ---

    @property
    def is_ramping(self) -> bool:
        """Return True while a ramp is in progress (device fan < target fan)."""
        return self._task is not None and not self._task.done()

    def display_fan_speed(self, fallback: int) -> int:
        """Fan speed to show in HA: the requested target while ramping."""
        if self.is_ramping and self._target_fan_speed is not None:
            return self._target_fan_speed
        return fallback

    def cancel(self) -> None:
        """Cancel any ramp in progress."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def set_floor(self, floor: int = WARM_ON_FLOOR) -> None:
        """Drop the device fan to the warm-up floor, bypassing the ramp.

        Used when warming on from standby so a subsequent target ramps up from a
        low airflow.
        """
        self.cancel()
        await self._device.set_fan_speed(floor)
        self._notify()

    async def request_fan_speed(self, target: int) -> None:
        """Apply a fan-speed request: ramp up gradually, apply decreases at once."""
        target = max(5, min(100, round(target / 5) * 5))
        self._target_fan_speed = target
        state = self._device.state
        current = state.fan_speed
        # Only ramp when warming: a fan increase while the target temp is at or
        # above the current temp. If the target is below current there is nothing
        # to maintain, so apply the new speed at once.
        warming = state.target_temperature >= state.current_temperature

        if not self._enabled or target <= current or not warming:
            # Disabled, a decrease / no-op, or not warming -> apply immediately.
            self.cancel()
            await self._device.set_fan_speed(target)
            self._notify()
            return

        # Increase while warming -> start (or restart, retargeting) the ramp.
        self.cancel()
        self._task = self._device.loop.create_task(self._ramp(target))
        self._notify()

    async def _ramp(self, target: int) -> None:
        """Step the fan speed up to ``target``, gated on temperature when heating."""
        _LOGGER.debug("Fan ramp: starting ramp to %d%%", target)
        try:
            # Track the level locally so the loop always terminates even if the
            # device stops reporting (e.g. a disconnect mid-ramp).
            level = self._device.state.fan_speed
            while level < target:
                level = min(level + RAMP_STEP, target)
                await self._device.set_fan_speed(level)
                self._notify()
                await asyncio.sleep(RAMP_STEP_DELAY)
                if self._device.state.operating_mode in HEATING_MODES:
                    await self._wait_for_temp_catchup()
            _LOGGER.debug("Fan ramp: reached target %d%%", target)
        except asyncio.CancelledError:
            _LOGGER.debug("Fan ramp: cancelled")
            raise
        finally:
            # Only clear the handle if a newer ramp hasn't already replaced it
            # (cancel-then-restart races this finally).
            if self._task is asyncio.current_task():
                self._task = None
            self._notify()

    async def _wait_for_temp_catchup(self) -> None:
        """Block until the current temp reaches the target (bounded by a timeout)."""
        target_temp = self._device.state.target_temperature
        try:
            async with asyncio.timeout(TEMP_CATCHUP_TIMEOUT):
                while self._device.state.current_temperature < target_temp:
                    await asyncio.sleep(1.0)
        except TimeoutError:
            _LOGGER.debug(
                "Fan ramp: current temp did not reach %.1f within %.0fs, continuing",
                target_temp,
                TEMP_CATCHUP_TIMEOUT,
            )
