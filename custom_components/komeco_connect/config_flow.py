"""Config flow for Komeco Connect."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KomecoApiClient, KomecoApiError, KomecoAuthError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_ID_TOKEN,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_SUB,
    CONF_DEVICE_ID,
    CONF_PLACE_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}" if len(local) > 2 else f"***@{domain}"


class KomecoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Komeco Connect."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_email = str(user_input.get(CONF_EMAIL, "")).strip()
            _LOGGER.debug("Starting config flow login for email=%s", _mask_email(raw_email))
            try:
                session = async_get_clientsession(self.hass)
                api = KomecoApiClient(
                    session=session,
                    email=user_input[CONF_EMAIL],
                    password=(user_input.get(CONF_PASSWORD) or "").strip(),
                    refresh_token=(user_input.get(CONF_REFRESH_TOKEN) or "").strip(),
                    device_id=None,
                    place_id=None,
                )
                password = (user_input.get(CONF_PASSWORD) or "").strip()
                if password:
                    await api.async_login_with_password(password)
                else:
                    await api.async_authenticate()

                heater_name = "Komeco Device"
                discovered = await api.async_discover_heaters()
                if not discovered:
                    errors["base"] = "no_devices"
                    _LOGGER.debug("Config flow discovered no supported heater devices")
                else:
                    first = discovered[0]
                    device_id = first["device_id"]
                    place_id = first["place_id"]
                    heater_name = first["device_name"] or heater_name

                if not errors:
                    await self.async_set_unique_id(device_id)
                    self._abort_if_unique_id_configured()

                    data = {
                        CONF_EMAIL: user_input[CONF_EMAIL].strip(),
                        CONF_PASSWORD: password,
                        CONF_REFRESH_TOKEN: api.refresh_token,
                        CONF_DEVICE_ID: device_id,
                        CONF_PLACE_ID: place_id,
                    }
                    token_data = api.token_data
                    if token_data.get("id_token"):
                        data[CONF_ID_TOKEN] = token_data["id_token"]
                    if token_data.get("access_token"):
                        data[CONF_ACCESS_TOKEN] = token_data["access_token"]
                    if token_data.get("sub"):
                        data[CONF_SUB] = token_data["sub"]

                    title = heater_name if heater_name else f"Komeco {device_id}"
                    _LOGGER.debug("Config flow succeeded device_id=%s place_id=%s", device_id, place_id)
                    return self.async_create_entry(title=title, data=data)

            except KomecoAuthError:
                _LOGGER.debug("Config flow auth failed", exc_info=True)
                errors["base"] = "invalid_auth"
            except KomecoApiError:
                _LOGGER.debug("Config flow API connectivity failed", exc_info=True)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.debug("Config flow unexpected error", exc_info=True)
                errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
