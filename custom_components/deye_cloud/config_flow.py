"""Configuration and compatible options flow for Deye Cloud."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import DeyeCloudApiError, DeyeCloudClient
from .const import (
    CONF_APP_ID, CONF_APP_SECRET, CONF_EMAIL, CONF_PASSWORD, CONF_REGION,
    DEFAULT_SCAN_INTERVAL, DOMAIN, REGIONS, REGION_EU,
)

_LOGGER = logging.getLogger(__name__)


class DeyeCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            client = DeyeCloudClient(
                base_url=REGIONS[user_input[CONF_REGION]]["base_url"],
                app_id=user_input[CONF_APP_ID],
                app_secret=user_input[CONF_APP_SECRET],
                email=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
            )
            try:
                if await client.test_connection():
                    await self.async_set_unique_id(user_input[CONF_EMAIL])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Deye Cloud ({user_input[CONF_EMAIL]})",
                        data={k: user_input[k] for k in (
                            CONF_REGION, CONF_APP_ID, CONF_APP_SECRET, CONF_EMAIL, CONF_PASSWORD
                        )},
                        options={CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)},
                    )
                errors["base"] = "cannot_connect"
            except DeyeCloudApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected Deye Cloud login error")
                errors["base"] = "unknown"
            finally:
                await client.close()
        region_options = [
            {"label": info["name"], "value": key} for key, info in REGIONS.items()
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_REGION, default=REGION_EU): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=region_options,
                                                  mode=selector.SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_APP_ID): str,
                vol.Required(CONF_APP_SECRET): str,
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=30, max=3600)
                ),
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        # HA 2025.12+ owns OptionsFlow.config_entry: DO NOT assign it yourself.
        return DeyeCloudOptionsFlow()


class DeyeCloudOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            }),
        )
