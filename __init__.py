"""Komeco Connect integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KomecoApiClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_ID_TOKEN,
    CONF_PASSWORD,
    CONF_PLACE_ID,
    CONF_REFRESH_TOKEN,
    CONF_SUB,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import KomecoDataUpdateCoordinator
from .realtime import KomecoRealtimeListener

KomecoConfigEntry = ConfigEntry
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: KomecoConfigEntry) -> bool:
    """Set up Komeco from a config entry."""
    _LOGGER.debug("Setting up Komeco entry_id=%s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})
    session = async_get_clientsession(hass)

    api = KomecoApiClient(
        session=session,
        email=entry.data[CONF_EMAIL],
        password=entry.data.get(CONF_PASSWORD),
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        device_id=entry.data.get(CONF_DEVICE_ID),
        place_id=entry.data.get(CONF_PLACE_ID),
        id_token=entry.data.get(CONF_ID_TOKEN),
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        sub=entry.data.get(CONF_SUB),
    )
    coordinator = KomecoDataUpdateCoordinator(hass=hass, entry=entry, api=api)
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.debug("Initial refresh completed for entry_id=%s", entry.entry_id)
    realtime = KomecoRealtimeListener(hass=hass, api=api, coordinator=coordinator)
    await realtime.async_start()
    _LOGGER.debug("Realtime listener started for entry_id=%s", entry.entry_id)

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "realtime": realtime,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KomecoConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Komeco entry_id=%s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    listener = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("realtime")
    if listener is not None:
        await listener.async_stop()
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    _LOGGER.debug("Unload finished for entry_id=%s unload_ok=%s", entry.entry_id, unload_ok)
    return unload_ok
