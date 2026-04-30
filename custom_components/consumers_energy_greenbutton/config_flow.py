"""Config flow for Consumers Energy Green Button integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ConsumersEnergyAPI, UtilityAPIError
from .const import DOMAIN, CONF_AUTHORIZATION_UID

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN, description={"suggested_value": ""}): str,
    }
)

STEP_AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AUTHORIZATION_UID): str,
    }
)


class ConsumersEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Consumers Energy Green Button."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_token: str | None = None
        self._authorizations: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step — enter API token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            session = async_get_clientsession(self.hass)
            api = ConsumersEnergyAPI(token, session)

            try:
                auths = await api.get_authorizations()
                if not auths:
                    errors["base"] = "no_authorizations"
                else:
                    self._api_token = token
                    self._authorizations = auths

                    if len(auths) == 1:
                        # Only one authorization — skip selection step
                        auth = auths[0]
                        return self._create_entry(auth)

                    return await self.async_step_select_auth()

            except UtilityAPIError:
                errors["base"] = "invalid_token"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "token_url": "https://utilityapi.com/dashboard"
            },
        )

    async def async_step_select_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let user pick which authorization to use if multiple exist."""
        errors: dict[str, str] = {}

        auth_options = {
            str(auth["uid"]): f"{auth.get('customer_name', 'Unknown')} "
            f"(Auth #{auth['uid']})"
            for auth in self._authorizations
        }

        if user_input is not None:
            uid = user_input[CONF_AUTHORIZATION_UID]
            auth = next(
                (a for a in self._authorizations if str(a["uid"]) == uid), None
            )
            if auth:
                return self._create_entry(auth)
            errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="select_auth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AUTHORIZATION_UID): vol.In(auth_options),
                }
            ),
            errors=errors,
        )

    def _create_entry(self, auth: dict) -> FlowResult:
        """Create the config entry."""
        uid = str(auth["uid"])
        customer = auth.get("customer_name", "Consumers Energy")

        return self.async_create_entry(
            title=f"{customer} ({uid})",
            data={
                CONF_TOKEN: self._api_token,
                CONF_AUTHORIZATION_UID: uid,
            },
        )
