from logging import getLogger
import voluptuous as vol
from typing import Any
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlowWithConfigEntry,
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
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import callback
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
)

_LOGGER = getLogger(__name__)


async def _validate_opensky(hass, client_id: str | None, client_secret: str | None) -> dict[str, str]:
    """Shared OpenSky credential validation for both the initial setup and options flow."""
    errors: dict[str, str] = {}
    if not client_id or not client_secret:
        errors['base'] = 'You need to pass both OpenSky client ID and secret'
        return errors
    try:
        opensky_client = OpenSkyClient(client_id, client_secret)
        await hass.async_add_executor_job(opensky_client.validate_credentials)
    except OpenSkyAuthError as error:
        _LOGGER.error('OpenSky Integration Exception - {}'.format(error))
        errors['base'] = str(error)
    return errors


class FlightRadarConfigFlow(ConfigFlow, domain=DOMAIN):

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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
                    vol.Required(CONF_SCAN_INTERVAL, default=10): int,
                    vol.Required(CONF_OPENSKY_CLIENT_ID): cv.string,
                    vol.Required(CONF_OPENSKY_CLIENT_SECRET): cv.string,
                }
            ),
            {
                CONF_LATITUDE: self.hass.config.latitude,
                CONF_LONGITUDE: self.hass.config.longitude,
            },
        ), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FlightRadarOptionsFlow(config_entry)


class FlightRadarOptionsFlow(OptionsFlowWithConfigEntry):

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)

        data_schema = vol.Schema({
            vol.Required(CONF_RADIUS, default=data.get(CONF_RADIUS)): vol.Coerce(float),
            vol.Required(CONF_LATITUDE, default=data.get(CONF_LATITUDE)): cv.latitude,
            vol.Required(CONF_LONGITUDE, default=data.get(CONF_LONGITUDE)): cv.longitude,
            vol.Required(CONF_SCAN_INTERVAL, default=data.get(CONF_SCAN_INTERVAL)): int,
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
            ): cv.string,
        })

        return self.async_show_form(step_id="init", data_schema=data_schema, errors=errors)
