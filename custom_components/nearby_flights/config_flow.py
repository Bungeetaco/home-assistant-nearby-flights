from collections.abc import Mapping
from logging import getLogger
import voluptuous as vol
from typing import Any
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from .const import (
    DOMAIN,
    DEFAULT_NAME,
    CONF_MIN_ALTITUDE,
    CONF_MAX_ALTITUDE,
    MIN_ALTITUDE,
    MAX_ALTITUDE,
    CONF_OPENSKY_CLIENT_ID,
    CONF_OPENSKY_CLIENT_SECRET,
)
from .api.opensky import OpenSkyClient, OpenSkyAuthError
import homeassistant.helpers.config_validation as cv
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
)

_LOGGER = getLogger(__name__)

# 60s keeps a single default-config install at 1,440 states/all calls/day,
# comfortably inside OpenSky's 4,000-credit daily budget; the old default of 10
# burned ~8,640 calls/day and deterministically rate-limited every install by
# midday. The minimum leaves headroom for users who knowingly poll faster.
DEFAULT_SCAN_INTERVAL_S = 60
MIN_SCAN_INTERVAL_S = 30

_SCAN_INTERVAL_VALIDATOR = vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_S))

_SECRET_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _validate_opensky(hass, client_id: str | None, client_secret: str | None) -> dict[str, str]:
    """Shared OpenSky credential validation for the setup, options and reauth flows."""
    errors: dict[str, str] = {}
    if not client_id or not client_secret:
        errors['base'] = 'missing_credentials'
        return errors
    opensky_client = OpenSkyClient(client_id, client_secret)
    try:
        await hass.async_add_executor_job(opensky_client.validate_credentials)
    except OpenSkyAuthError as error:
        _LOGGER.error('OpenSky rejected the supplied credentials: %s', error)
        errors['base'] = 'invalid_auth'
    except Exception as error:
        _LOGGER.error('Could not reach OpenSky to validate credentials: %s', error)
        errors['base'] = 'cannot_connect'
    finally:
        await hass.async_add_executor_job(opensky_client.close)
    return errors


class NearbyFlightsConfigFlow(ConfigFlow, domain=DOMAIN):

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate_opensky(
                self.hass,
                user_input.get(CONF_OPENSKY_CLIENT_ID),
                user_input.get(CONF_OPENSKY_CLIENT_SECRET),
            )
            if not errors:
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)

        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(
            vol.Schema(
                {
                    vol.Required(CONF_RADIUS, default=1000): vol.Coerce(float),
                    vol.Required(CONF_LATITUDE): cv.latitude,
                    vol.Required(CONF_LONGITUDE): cv.longitude,
                    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_S): _SCAN_INTERVAL_VALIDATOR,
                    vol.Required(CONF_OPENSKY_CLIENT_ID): cv.string,
                    vol.Required(CONF_OPENSKY_CLIENT_SECRET): _SECRET_SELECTOR,
                }
            ),
            {
                CONF_LATITUDE: self.hass.config.latitude,
                CONF_LONGITUDE: self.hass.config.longitude,
            },
        ), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Triggered by ConfigEntryAuthFailed (see coordinator.py)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if user_input is not None:
            errors = await _validate_opensky(
                self.hass,
                user_input.get(CONF_OPENSKY_CLIENT_ID),
                user_input.get(CONF_OPENSKY_CLIENT_SECRET),
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, **user_input},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OPENSKY_CLIENT_ID): cv.string,
                    vol.Required(CONF_OPENSKY_CLIENT_SECRET): _SECRET_SELECTOR,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NearbyFlightsOptionsFlow()


class NearbyFlightsOptionsFlow(OptionsFlow):

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        data = user_input or self.config_entry.data

        if user_input is not None:
            errors = await _validate_opensky(
                self.hass,
                data.get(CONF_OPENSKY_CLIENT_ID),
                data.get(CONF_OPENSKY_CLIENT_SECRET),
            )

            if not errors:
                self.hass.config_entries.async_update_entry(self.config_entry, data=user_input)
                # The real config lives in entry.data (updated above, which
                # already triggers the reload listener); keep entry.options
                # empty rather than duplicating the whole config - including
                # the OpenSky secret - into a second, unread store.
                return self.async_create_entry(data={})

        data_schema = vol.Schema({
            vol.Required(CONF_RADIUS, default=data.get(CONF_RADIUS)): vol.Coerce(float),
            vol.Required(CONF_LATITUDE, default=data.get(CONF_LATITUDE)): cv.latitude,
            vol.Required(CONF_LONGITUDE, default=data.get(CONF_LONGITUDE)): cv.longitude,
            vol.Required(CONF_SCAN_INTERVAL, default=data.get(CONF_SCAN_INTERVAL)): _SCAN_INTERVAL_VALIDATOR,
            vol.Optional(CONF_MIN_ALTITUDE,
                         description={"suggested_value": data.get(CONF_MIN_ALTITUDE, MIN_ALTITUDE)}): int,
            vol.Optional(CONF_MAX_ALTITUDE,
                         description={"suggested_value": data.get(CONF_MAX_ALTITUDE, MAX_ALTITUDE)}): int,
            vol.Required(
                CONF_OPENSKY_CLIENT_ID,
                description={"suggested_value": data.get(CONF_OPENSKY_CLIENT_ID, '')},
            ): cv.string,
            vol.Required(
                CONF_OPENSKY_CLIENT_SECRET,
                description={"suggested_value": data.get(CONF_OPENSKY_CLIENT_SECRET, '')},
            ): _SECRET_SELECTOR,
        })

        return self.async_show_form(step_id="init", data_schema=data_schema, errors=errors)
