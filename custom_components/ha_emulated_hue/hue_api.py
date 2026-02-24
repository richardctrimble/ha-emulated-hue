"""Hue REST API endpoints for Emulated Hue +.

Implements the Philips Hue bridge API so that Alexa (and other Hue clients)
can discover and control devices managed by HueDeviceManager.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
import hashlib
from http import HTTPStatus
from ipaddress import ip_address
import logging
import time
from typing import Any

from aiohttp import web

from homeassistant import core
from homeassistant.components import (
    climate,
    cover,
    fan,
    humidifier,
    light,
    media_player,
    scene,
    script,
)
from homeassistant.components.climate import (
    SERVICE_SET_TEMPERATURE,
    ClimateEntityFeature,
)
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverEntityFeature,
)
from homeassistant.components.fan import ATTR_PERCENTAGE, FanEntityFeature
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.components.humidifier import ATTR_HUMIDITY, SERVICE_SET_HUMIDITY
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.components.media_player import (
    ATTR_MEDIA_VOLUME_LEVEL,
    MediaPlayerEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_SET,
    STATE_CLOSED,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Event, EventStateChangedData, State
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import color as color_util
from homeassistant.util.json import json_loads
from homeassistant.util.network import is_local

from .const import (
    CACHE_TIMEOUT,
    DOMAIN,
    HUE_API_STATE_BRI_MAX,
    HUE_API_STATE_BRI_MIN,
    HUE_API_STATE_CT_MAX,
    HUE_API_STATE_CT_MIN,
    HUE_API_STATE_HUE_MAX,
    HUE_API_STATE_HUE_MIN,
    HUE_API_STATE_SAT_MAX,
    HUE_API_STATE_SAT_MIN,
    HUE_API_USERNAME,
    HUE_SERIAL_NUMBER,
    OFF_MAPS_TO_ON_DOMAINS,
    STATE_CHANGE_TIMEOUT,
)
from .hue_device import HueDevice
from .hue_device_manager import HueDeviceManager

_LOGGER = logging.getLogger(__name__)

_OFF_STATES: dict[str, str] = {cover.DOMAIN: STATE_CLOSED}

# Hue API state key names (as they appear in JSON requests/responses)
HUE_API_STATE_ON = "on"
HUE_API_STATE_BRI = "bri"
HUE_API_STATE_COLORMODE = "colormode"
HUE_API_STATE_HUE = "hue"
HUE_API_STATE_SAT = "sat"
HUE_API_STATE_CT = "ct"
HUE_API_STATE_XY = "xy"
HUE_API_STATE_EFFECT = "effect"
HUE_API_STATE_TRANSITION = "transitiontime"

UNAUTHORIZED_USER = [
    {"error": {"address": "/", "description": "unauthorized user", "type": 1}}
]


def _hue_api_error(
    error_type: int, address: str, description: str
) -> list[dict[str, Any]]:
    """Build a Hue API error response array.

    Error types from the Hue API spec:
      1 = unauthorized user
      3 = resource not available
      4 = method not available
      5 = missing parameters
      6 = parameter not available
      7 = invalid value
    """
    return [{"error": {"type": error_type, "address": address, "description": description}}]

DIMMABLE_SUPPORTED_FEATURES_BY_DOMAIN = {
    cover.DOMAIN: CoverEntityFeature.SET_POSITION,
    fan.DOMAIN: FanEntityFeature.SET_SPEED,
    media_player.DOMAIN: MediaPlayerEntityFeature.VOLUME_SET,
    climate.DOMAIN: ClimateEntityFeature.TARGET_TEMPERATURE,
}

ENTITY_FEATURES_BY_DOMAIN = {
    cover.DOMAIN: CoverEntityFeature,
    fan.DOMAIN: FanEntityFeature,
    media_player.DOMAIN: MediaPlayerEntityFeature,
    climate.DOMAIN: ClimateEntityFeature,
}

# Key used to store the device manager reference in the aiohttp app
KEY_DEVICE_MANAGER = "ha_emulated_hue_device_manager"
KEY_CACHED_STATES = "ha_emulated_hue_cached_states"
KEY_ADVERTISE_IP = "ha_emulated_hue_advertise_ip"
KEY_ADVERTISE_PORT = "ha_emulated_hue_advertise_port"


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


@lru_cache(maxsize=32)
def _remote_is_allowed(address: str) -> bool:
    """Only allow requests from the local network."""
    return is_local(ip_address(address))


# ---------------------------------------------------------------------------
# View classes — registered on the standalone aiohttp app
# ---------------------------------------------------------------------------


class HueUsernameView(HomeAssistantView):
    """Handle POST /api — fake username/pairing creation."""

    url = "/api"
    name = "ha_emulated_hue:api:create_username"
    extra_urls = ["/api/"]
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        """Handle a POST request."""
        assert request.remote is not None
        if not _remote_is_allowed(request.remote):
            return self.json_message("Only local IPs allowed", HTTPStatus.UNAUTHORIZED)

        try:
            data = await request.json(loads=json_loads)
        except ValueError:
            return self.json_message("Invalid JSON", HTTPStatus.BAD_REQUEST)

        if "devicetype" not in data:
            return self.json_message("devicetype not specified", HTTPStatus.BAD_REQUEST)

        return self.json([{"success": {"username": HUE_API_USERNAME}}])


class HueUnauthorizedUser(HomeAssistantView):
    """Handle GET /api — return unauthorized error."""

    url = "/api"
    name = "ha_emulated_hue:api:unauthorized_user"
    extra_urls = ["/api/"]
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Handle a GET request."""
        return self.json(UNAUTHORIZED_USER)


class HueConfigView(HomeAssistantView):
    """Handle GET /api/{username}/config — bridge configuration."""

    url = "/api/{username}/config"
    extra_urls = ["/api/config"]
    name = "ha_emulated_hue:username:config"
    requires_auth = False

    @core.callback
    def get(self, request: web.Request, username: str = "") -> web.Response:
        """Handle a GET request."""
        assert request.remote is not None
        if not _remote_is_allowed(request.remote):
            return self.json_message("Only local IPs allowed", HTTPStatus.UNAUTHORIZED)

        return self.json(_create_config_model(request))


class HueAllLightsStateView(HomeAssistantView):
    """Handle GET /api/{username}/lights — list all devices."""

    url = "/api/{username}/lights"
    name = "ha_emulated_hue:lights:state"
    requires_auth = False

    @core.callback
    def get(self, request: web.Request, username: str) -> web.Response:
        """Handle a GET request."""
        assert request.remote is not None
        if not _remote_is_allowed(request.remote):
            return self.json_message("Only local IPs allowed", HTTPStatus.UNAUTHORIZED)

        return self.json(_create_list_of_entities(request))


class HueOneLightStateView(HomeAssistantView):
    """Handle GET /api/{username}/lights/{entity_id} — single device state."""

    url = "/api/{username}/lights/{entity_id}"
    name = "ha_emulated_hue:light:state"
    requires_auth = False

    @core.callback
    def get(self, request: web.Request, username: str, entity_id: str) -> web.Response:
        """Handle a GET request."""
        assert request.remote is not None
        if not _remote_is_allowed(request.remote):
            return self.json_message("Only local IPs allowed", HTTPStatus.UNAUTHORIZED)

        hass = request.app[KEY_HASS]
        device_manager: HueDeviceManager = request.app[KEY_DEVICE_MANAGER]

        device = device_manager.get_device(entity_id)
        if device is None:
            _LOGGER.debug("Unknown device number: %s", entity_id)
            return self.json(
                _hue_api_error(
                    3,
                    f"/lights/{entity_id}",
                    f"resource, /lights/{entity_id}, not available",
                ),
                status_code=HTTPStatus.NOT_FOUND,
            )

        device.record_access(request.remote)

        return self.json(
            device_to_json(hass, device, request.app[KEY_CACHED_STATES])
        )


class HueFullStateView(HomeAssistantView):
    """Handle GET /api/{username} — full bridge state."""

    url = "/api/{username}"
    name = "ha_emulated_hue:username:state"
    requires_auth = False

    @core.callback
    def get(self, request: web.Request, username: str) -> web.Response:
        """Handle a GET request."""
        assert request.remote is not None
        if not _remote_is_allowed(request.remote):
            return self.json_message("Only local IPs allowed", HTTPStatus.UNAUTHORIZED)

        if username != HUE_API_USERNAME:
            return self.json(UNAUTHORIZED_USER)

        return self.json(
            {
                "lights": _create_list_of_entities(request),
                "config": _create_config_model(request),
            }
        )


class HueAllGroupsStateView(HomeAssistantView):
    """Handle GET /api/{username}/groups — empty groups for compatibility."""

    url = "/api/{username}/groups"
    name = "ha_emulated_hue:all_groups:state"
    requires_auth = False

    @core.callback
    def get(self, request: web.Request, username: str) -> web.Response:
        """Handle a GET request."""
        assert request.remote is not None
        if not _remote_is_allowed(request.remote):
            return self.json_message("Only local IPs allowed", HTTPStatus.UNAUTHORIZED)

        return self.json({})


class HueGroupView(HomeAssistantView):
    """Handle PUT /api/{username}/groups/0/action — stub for Logitech Pop."""

    url = "/api/{username}/groups/0/action"
    name = "ha_emulated_hue:groups:state"
    requires_auth = False

    @core.callback
    def put(self, request: web.Request, username: str) -> web.Response:
        """Handle a PUT request."""
        assert request.remote is not None
        if not _remote_is_allowed(request.remote):
            return self.json_message("Only local IPs allowed", HTTPStatus.UNAUTHORIZED)

        return self.json(
            [
                {
                    "error": {
                        "address": "/groups/0/action/scene",
                        "type": 7,
                        "description": "invalid value, dummy for parameter, scene",
                    }
                }
            ]
        )


class HueOneLightChangeView(HomeAssistantView):
    """Handle PUT /api/{username}/lights/{entity_number}/state — control a device."""

    url = "/api/{username}/lights/{entity_number}/state"
    name = "ha_emulated_hue:light:change"
    requires_auth = False

    async def put(
        self, request: web.Request, username: str, entity_number: str
    ) -> web.Response:
        """Process a request to set the state of an individual light."""
        assert request.remote is not None
        if not _remote_is_allowed(request.remote):
            return self.json_message("Only local IPs allowed", HTTPStatus.UNAUTHORIZED)

        hass: core.HomeAssistant = request.app[KEY_HASS]
        device_manager: HueDeviceManager = request.app[KEY_DEVICE_MANAGER]
        cached_states: dict[str, list] = request.app[KEY_CACHED_STATES]

        device = device_manager.get_device(entity_number)
        if device is None:
            _LOGGER.debug("Unknown device number: %s", entity_number)
            return self.json(
                _hue_api_error(
                    3,
                    f"/lights/{entity_number}",
                    f"resource, /lights/{entity_number}, not available",
                ),
                status_code=HTTPStatus.NOT_FOUND,
            )

        device.record_access(request.remote)

        if not device.entity_id:
            _LOGGER.warning("Device %s is not linked to an entity", entity_number)
            return self.json(
                _hue_api_error(
                    3,
                    f"/lights/{entity_number}",
                    f"resource, /lights/{entity_number}, not available",
                ),
                status_code=HTTPStatus.NOT_FOUND,
            )

        entity_id = device.entity_id
        entity = hass.states.get(entity_id)
        if entity is None:
            _LOGGER.warning("Entity not found: %s", entity_id)
            return self.json(
                _hue_api_error(
                    3,
                    f"/lights/{entity_number}",
                    f"resource, /lights/{entity_number}, not available",
                ),
                status_code=HTTPStatus.NOT_FOUND,
            )

        try:
            request_json = await request.json()
        except ValueError:
            _LOGGER.error("Received invalid json")
            return self.json_message("Invalid JSON", HTTPStatus.BAD_REQUEST)

        # Get entity capabilities
        entity_features = entity.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
        if entity.domain == light.DOMAIN:
            color_modes = (
                entity.attributes.get(light.ATTR_SUPPORTED_COLOR_MODES) or []
            )

        # Parse the incoming Hue API request
        parsed: dict[str, Any] = {
            HUE_API_STATE_ON: False,
            HUE_API_STATE_BRI: None,
            HUE_API_STATE_HUE: None,
            HUE_API_STATE_SAT: None,
            HUE_API_STATE_CT: None,
            HUE_API_STATE_XY: None,
            HUE_API_STATE_TRANSITION: None,
        }

        if HUE_API_STATE_ON in request_json:
            if not isinstance(request_json[HUE_API_STATE_ON], bool):
                _LOGGER.error("Unable to parse data: %s", request_json)
                return self.json_message("Bad request", HTTPStatus.BAD_REQUEST)
            parsed[HUE_API_STATE_ON] = request_json[HUE_API_STATE_ON]
        else:
            parsed[HUE_API_STATE_ON] = _hass_to_hue_state(entity)

        for key in (
            HUE_API_STATE_BRI,
            HUE_API_STATE_HUE,
            HUE_API_STATE_SAT,
            HUE_API_STATE_CT,
            HUE_API_STATE_TRANSITION,
        ):
            if key in request_json:
                try:
                    parsed[key] = int(request_json[key])
                except ValueError:
                    _LOGGER.error("Unable to parse data: %s", request_json)
                    return self.json_message("Bad request", HTTPStatus.BAD_REQUEST)

        if HUE_API_STATE_XY in request_json:
            try:
                parsed[HUE_API_STATE_XY] = (
                    float(request_json[HUE_API_STATE_XY][0]),
                    float(request_json[HUE_API_STATE_XY][1]),
                )
            except ValueError:
                _LOGGER.error("Unable to parse data: %s", request_json)
                return self.json_message("Bad request", HTTPStatus.BAD_REQUEST)

        # Domain-specific brightness interpretation
        if HUE_API_STATE_BRI in request_json:
            if entity.domain == light.DOMAIN:
                if light.brightness_supported(color_modes):
                    parsed[HUE_API_STATE_ON] = parsed[HUE_API_STATE_BRI] > 0
                else:
                    parsed[HUE_API_STATE_BRI] = None

            elif entity.domain == scene.DOMAIN:
                parsed[HUE_API_STATE_BRI] = None
                parsed[HUE_API_STATE_ON] = True

            elif entity.domain in (
                script.DOMAIN,
                media_player.DOMAIN,
                fan.DOMAIN,
                cover.DOMAIN,
                climate.DOMAIN,
                humidifier.DOMAIN,
            ):
                # Convert 0-254 to 0-100
                level = (parsed[HUE_API_STATE_BRI] / HUE_API_STATE_BRI_MAX) * 100
                parsed[HUE_API_STATE_BRI] = round(level)
                parsed[HUE_API_STATE_ON] = True

        # Choose HA domain and service
        domain = core.DOMAIN
        turn_on_needed = False
        service: str | None = (
            SERVICE_TURN_ON if parsed[HUE_API_STATE_ON] else SERVICE_TURN_OFF
        )
        data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id}

        # --- Light ---
        if entity.domain == light.DOMAIN:
            if parsed[HUE_API_STATE_ON]:
                if (
                    light.brightness_supported(color_modes)
                    and parsed[HUE_API_STATE_BRI] is not None
                ):
                    data[ATTR_BRIGHTNESS] = hue_brightness_to_hass(
                        parsed[HUE_API_STATE_BRI]
                    )

                if light.color_supported(color_modes):
                    if any((parsed[HUE_API_STATE_HUE], parsed[HUE_API_STATE_SAT])):
                        hue = parsed[HUE_API_STATE_HUE] or 0
                        sat = parsed[HUE_API_STATE_SAT] or 0
                        hue = int((hue / HUE_API_STATE_HUE_MAX) * 360)
                        sat = int((sat / HUE_API_STATE_SAT_MAX) * 100)
                        data[ATTR_HS_COLOR] = (hue, sat)

                    if parsed[HUE_API_STATE_XY] is not None:
                        data[ATTR_XY_COLOR] = parsed[HUE_API_STATE_XY]

                if (
                    light.color_temp_supported(color_modes)
                    and parsed[HUE_API_STATE_CT] is not None
                ):
                    data[ATTR_COLOR_TEMP_KELVIN] = (
                        color_util.color_temperature_mired_to_kelvin(
                            parsed[HUE_API_STATE_CT]
                        )
                    )

                if (
                    entity_features & LightEntityFeature.TRANSITION
                    and parsed[HUE_API_STATE_TRANSITION] is not None
                ):
                    data[ATTR_TRANSITION] = parsed[HUE_API_STATE_TRANSITION] / 10

        # --- Script ---
        elif entity.domain == script.DOMAIN:
            data["variables"] = {
                "requested_state": STATE_ON if parsed[HUE_API_STATE_ON] else STATE_OFF
            }
            if parsed[HUE_API_STATE_BRI] is not None:
                data["variables"]["requested_level"] = parsed[HUE_API_STATE_BRI]

        # --- Climate ---
        elif entity.domain == climate.DOMAIN:
            service = None
            if (
                entity_features & ClimateEntityFeature.TARGET_TEMPERATURE
                and parsed[HUE_API_STATE_BRI] is not None
            ):
                domain = entity.domain
                service = SERVICE_SET_TEMPERATURE
                data[ATTR_TEMPERATURE] = parsed[HUE_API_STATE_BRI]

        # --- Humidifier ---
        elif entity.domain == humidifier.DOMAIN:
            if parsed[HUE_API_STATE_BRI] is not None:
                turn_on_needed = True
                domain = entity.domain
                service = SERVICE_SET_HUMIDITY
                data[ATTR_HUMIDITY] = parsed[HUE_API_STATE_BRI]

        # --- Media Player ---
        elif entity.domain == media_player.DOMAIN:
            if (
                entity_features & MediaPlayerEntityFeature.VOLUME_SET
                and parsed[HUE_API_STATE_BRI] is not None
            ):
                turn_on_needed = True
                domain = entity.domain
                service = SERVICE_VOLUME_SET
                data[ATTR_MEDIA_VOLUME_LEVEL] = parsed[HUE_API_STATE_BRI] / 100.0

        # --- Cover ---
        elif entity.domain == cover.DOMAIN:
            domain = entity.domain
            if service == SERVICE_TURN_ON:
                service = SERVICE_OPEN_COVER
            else:
                service = SERVICE_CLOSE_COVER

            if (
                entity_features & CoverEntityFeature.SET_POSITION
                and parsed[HUE_API_STATE_BRI] is not None
            ):
                service = SERVICE_SET_COVER_POSITION
                data[ATTR_POSITION] = parsed[HUE_API_STATE_BRI]

        # --- Fan ---
        elif (
            entity.domain == fan.DOMAIN
            and entity_features & FanEntityFeature.SET_SPEED
            and parsed[HUE_API_STATE_BRI] is not None
        ):
            domain = entity.domain
            data[ATTR_PERCENTAGE] = parsed[HUE_API_STATE_BRI]

        # Map off → on for stateless domains (scene, script)
        if entity.domain in OFF_MAPS_TO_ON_DOMAINS:
            service = SERVICE_TURN_ON

        # Some domains need a separate turn_on first
        if turn_on_needed:
            await hass.services.async_call(
                core.DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=False,
            )

        if service is not None:
            state_will_change = parsed[HUE_API_STATE_ON] != _hass_to_hue_state(entity)

            await hass.services.async_call(domain, service, data, blocking=False)

            if state_will_change:
                await _wait_for_state_change_or_timeout(
                    hass, entity_id, CACHE_TIMEOUT
                )

        # Build success responses
        json_response = [
            _create_hue_success_response(
                entity_number, HUE_API_STATE_ON, parsed[HUE_API_STATE_ON]
            )
        ]
        for key in (
            HUE_API_STATE_BRI,
            HUE_API_STATE_HUE,
            HUE_API_STATE_SAT,
            HUE_API_STATE_CT,
            HUE_API_STATE_XY,
            HUE_API_STATE_TRANSITION,
        ):
            if parsed[key] is not None:
                json_response.append(
                    _create_hue_success_response(entity_number, key, parsed[key])
                )

        # Cache the state
        if entity.domain in OFF_MAPS_TO_ON_DOMAINS:
            cached_states[entity_id] = [parsed, None]
        else:
            cached_states[entity_id] = [parsed, time.time()]

        return self.json(json_response)


# ---------------------------------------------------------------------------
# State conversion helpers
# ---------------------------------------------------------------------------


def device_to_json(
    hass: core.HomeAssistant,
    device: HueDevice,
    cached_states: dict[str, list],
) -> dict[str, Any]:
    """Convert a HueDevice to full Hue bridge JSON representation."""
    unique_id = _entity_unique_id(device.hue_id)

    # Unlinked device — report as off / unreachable dimmable light
    if not device.entity_id:
        return {
            "state": {
                HUE_API_STATE_ON: False,
                "reachable": False,
                "mode": "homeautomation",
                HUE_API_STATE_BRI: 0,
            },
            "name": device.name,
            "uniqueid": unique_id,
            "manufacturername": "Home Assistant",
            "swversion": "123",
            "type": "Dimmable light",
            "modelid": "HASS123",
        }

    state = hass.states.get(device.entity_id)
    if state is None:
        return {
            "state": {
                HUE_API_STATE_ON: False,
                "reachable": False,
                "mode": "homeautomation",
                HUE_API_STATE_BRI: 0,
            },
            "name": device.name,
            "uniqueid": unique_id,
            "manufacturername": "Home Assistant",
            "swversion": "123",
            "type": "Dimmable light",
            "modelid": "HASS123",
        }

    color_modes = state.attributes.get(light.ATTR_SUPPORTED_COLOR_MODES) or []
    state_dict = _get_entity_state_dict(state, cached_states)

    json_state: dict[str, str | bool | int] = {
        HUE_API_STATE_ON: state_dict[HUE_API_STATE_ON],
        "reachable": state.state != STATE_UNAVAILABLE,
        "mode": "homeautomation",
    }

    retval: dict[str, Any] = {
        "state": json_state,
        "name": device.name,
        "uniqueid": unique_id,
        "manufacturername": "Home Assistant",
        "swversion": "123",
    }

    is_light_domain = state.domain == light.DOMAIN
    color_supported = is_light_domain and light.color_supported(color_modes)
    color_temp_supported = is_light_domain and light.color_temp_supported(color_modes)

    if color_supported and color_temp_supported:
        retval["type"] = "Extended color light"
        retval["modelid"] = "HASS231"
        json_state.update(
            {
                HUE_API_STATE_BRI: state_dict[HUE_API_STATE_BRI],
                HUE_API_STATE_HUE: state_dict[HUE_API_STATE_HUE],
                HUE_API_STATE_SAT: state_dict[HUE_API_STATE_SAT],
                HUE_API_STATE_CT: state_dict[HUE_API_STATE_CT],
                HUE_API_STATE_EFFECT: "none",
            }
        )
        if state_dict[HUE_API_STATE_HUE] > 0 or state_dict[HUE_API_STATE_SAT] > 0:
            json_state[HUE_API_STATE_COLORMODE] = "hs"
        else:
            json_state[HUE_API_STATE_COLORMODE] = "ct"

    elif color_supported:
        retval["type"] = "Color light"
        retval["modelid"] = "HASS213"
        json_state.update(
            {
                HUE_API_STATE_BRI: state_dict[HUE_API_STATE_BRI],
                HUE_API_STATE_COLORMODE: "hs",
                HUE_API_STATE_HUE: state_dict[HUE_API_STATE_HUE],
                HUE_API_STATE_SAT: state_dict[HUE_API_STATE_SAT],
                HUE_API_STATE_EFFECT: "none",
            }
        )

    elif color_temp_supported:
        retval["type"] = "Color temperature light"
        retval["modelid"] = "HASS312"
        json_state.update(
            {
                HUE_API_STATE_COLORMODE: "ct",
                HUE_API_STATE_CT: state_dict[HUE_API_STATE_CT],
                HUE_API_STATE_BRI: state_dict[HUE_API_STATE_BRI],
            }
        )

    elif _state_supports_hue_brightness(state, color_modes):
        retval["type"] = "Dimmable light"
        retval["modelid"] = "HASS123"
        json_state.update({HUE_API_STATE_BRI: state_dict[HUE_API_STATE_BRI]})

    else:
        # Entity doesn't support brightness — report as On/Off light
        retval["type"] = "On/Off light"
        retval["productname"] = "On/Off light"
        retval["modelid"] = "HASS321"

    return retval


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_entity_state_dict(
    entity: State,
    cached_states: dict[str, list],
) -> dict[str, Any]:
    """Get the state dict for an entity, respecting the short-lived cache."""
    cached_entry = cached_states.get(entity.entity_id)
    cached = None

    if cached_entry is not None:
        entry_state, entry_time = cached_entry
        if entry_time is None:
            # Permanent cache for off-maps-to-on domains
            cached = entry_state
        elif (
            time.time() - entry_time < CACHE_TIMEOUT
            and entry_state[HUE_API_STATE_ON] == _hass_to_hue_state(entity)
        ):
            cached = entry_state
        else:
            cached_states.pop(entity.entity_id)

    if cached is None:
        return _build_entity_state_dict(entity)

    data: dict[str, Any] = cached
    if data[HUE_API_STATE_BRI] is None:
        data[HUE_API_STATE_BRI] = HUE_API_STATE_BRI_MAX if data[HUE_API_STATE_ON] else 0
    if data[HUE_API_STATE_HUE] is None or data[HUE_API_STATE_SAT] is None:
        data[HUE_API_STATE_HUE] = 0
        data[HUE_API_STATE_SAT] = 0
    if data[HUE_API_STATE_BRI] == 0:
        data[HUE_API_STATE_HUE] = 0
        data[HUE_API_STATE_SAT] = 0
    _clamp_values(data)
    return data


def _build_entity_state_dict(entity: State) -> dict[str, Any]:
    """Build a state dict from current HA entity state."""
    is_on = _hass_to_hue_state(entity)
    data: dict[str, Any] = {
        HUE_API_STATE_ON: is_on,
        HUE_API_STATE_BRI: None,
        HUE_API_STATE_HUE: None,
        HUE_API_STATE_SAT: None,
        HUE_API_STATE_CT: None,
    }
    attributes = entity.attributes

    if is_on:
        data[HUE_API_STATE_BRI] = hass_to_hue_brightness(
            attributes.get(ATTR_BRIGHTNESS) or 0
        )
        if (hue_sat := attributes.get(ATTR_HS_COLOR)) is not None:
            h, s = hue_sat
            data[HUE_API_STATE_HUE] = int((h / 360.0) * HUE_API_STATE_HUE_MAX)
            data[HUE_API_STATE_SAT] = int((s / 100.0) * HUE_API_STATE_SAT_MAX)
        else:
            data[HUE_API_STATE_HUE] = HUE_API_STATE_HUE_MIN
            data[HUE_API_STATE_SAT] = HUE_API_STATE_SAT_MIN

        kelvin = attributes.get(ATTR_COLOR_TEMP_KELVIN)
        data[HUE_API_STATE_CT] = (
            color_util.color_temperature_kelvin_to_mired(kelvin)
            if kelvin is not None
            else 0
        )
    else:
        data[HUE_API_STATE_BRI] = 0
        data[HUE_API_STATE_HUE] = 0
        data[HUE_API_STATE_SAT] = 0
        data[HUE_API_STATE_CT] = 0

    # Domain-specific brightness overrides
    if entity.domain == climate.DOMAIN:
        temperature = attributes.get(ATTR_TEMPERATURE, 0)
        data[HUE_API_STATE_BRI] = round(temperature * HUE_API_STATE_BRI_MAX / 100)
    elif entity.domain == humidifier.DOMAIN:
        humidity = attributes.get(ATTR_HUMIDITY, 0)
        data[HUE_API_STATE_BRI] = round(humidity * HUE_API_STATE_BRI_MAX / 100)
    elif entity.domain == media_player.DOMAIN:
        level = attributes.get(ATTR_MEDIA_VOLUME_LEVEL, 1.0 if is_on else 0.0)
        data[HUE_API_STATE_BRI] = round(min(1.0, level) * HUE_API_STATE_BRI_MAX)
    elif entity.domain == fan.DOMAIN:
        percentage = attributes.get(ATTR_PERCENTAGE) or 0
        data[HUE_API_STATE_BRI] = round(percentage * HUE_API_STATE_BRI_MAX / 100)
    elif entity.domain == cover.DOMAIN:
        level = attributes.get(ATTR_CURRENT_POSITION, 0)
        data[HUE_API_STATE_BRI] = round(level / 100 * HUE_API_STATE_BRI_MAX)

    _clamp_values(data)
    return data


def _clamp_values(data: dict[str, Any]) -> None:
    """Clamp brightness, hue, saturation, and color temp to valid API ranges."""
    for key, v_min, v_max in (
        (HUE_API_STATE_BRI, HUE_API_STATE_BRI_MIN, HUE_API_STATE_BRI_MAX),
        (HUE_API_STATE_HUE, HUE_API_STATE_HUE_MIN, HUE_API_STATE_HUE_MAX),
        (HUE_API_STATE_SAT, HUE_API_STATE_SAT_MIN, HUE_API_STATE_SAT_MAX),
        (HUE_API_STATE_CT, HUE_API_STATE_CT_MIN, HUE_API_STATE_CT_MAX),
    ):
        if data[key] is not None:
            data[key] = max(v_min, min(data[key], v_max))


def _state_supports_hue_brightness(
    state: State, color_modes: list[ColorMode],
) -> bool:
    """Return True if the entity supports brightness in Hue terms."""
    domain = state.domain
    if domain == light.DOMAIN:
        return light.brightness_supported(color_modes)
    if not (required_feature := DIMMABLE_SUPPORTED_FEATURES_BY_DOMAIN.get(domain)):
        return False
    features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
    enum = ENTITY_FEATURES_BY_DOMAIN[domain]
    features = enum(features) if type(features) is int else features
    return required_feature in features


def _hass_to_hue_state(entity: State) -> bool:
    """Convert HA entity state to Hue on/off boolean."""
    return entity.state != _OFF_STATES.get(entity.domain, STATE_OFF)


@lru_cache(maxsize=1024)
def _entity_unique_id(hue_id: str) -> str:
    """Generate a Hue-format unique ID from a device's hue_id."""
    unique_id = hashlib.md5(
        f"ha_emulated_hue_{hue_id}".encode()
    ).hexdigest()
    return (
        f"00:{unique_id[0:2]}:{unique_id[2:4]}:"
        f"{unique_id[4:6]}:{unique_id[6:8]}:{unique_id[8:10]}:"
        f"{unique_id[10:12]}:{unique_id[12:14]}-{unique_id[14:16]}"
    )


def _create_hue_success_response(
    entity_number: str, attr: str, value: Any,
) -> dict[str, Any]:
    """Create a success response for an attribute set on a light."""
    success_key = f"/lights/{entity_number}/state/{attr}"
    return {"success": {success_key: value}}


def _create_config_model(request: web.Request) -> dict[str, Any]:
    """Create the bridge config response."""
    advertise_ip = request.app[KEY_ADVERTISE_IP]
    advertise_port = request.app[KEY_ADVERTISE_PORT]
    return {
        "name": "HASS BRIDGE",
        "mac": "00:00:00:00:00:00",
        "swversion": "01003542",
        "apiversion": "1.17.0",
        "whitelist": {HUE_API_USERNAME: {"name": "HASS BRIDGE"}},
        "ipaddress": f"{advertise_ip}:{advertise_port}",
        "linkbutton": True,
    }


def _create_list_of_entities(request: web.Request) -> dict[str, Any]:
    """Create a dict of all linked devices as Hue light resources."""
    hass = request.app[KEY_HASS]
    device_manager: HueDeviceManager = request.app[KEY_DEVICE_MANAGER]
    cached_states: dict[str, list] = request.app[KEY_CACHED_STATES]

    result: dict[str, Any] = {}
    for device in device_manager.get_all_devices():
        if device.is_linked:
            result[device.hue_id] = device_to_json(hass, device, cached_states)
    return result


def hue_brightness_to_hass(value: int) -> int:
    """Convert Hue brightness 1..254 to HA format 0..255."""
    return min(255, round((value / HUE_API_STATE_BRI_MAX) * 255))


def hass_to_hue_brightness(value: int) -> int:
    """Convert HA brightness 0..255 to Hue 1..254 scale."""
    return max(1, round((value / 255) * HUE_API_STATE_BRI_MAX))


async def _wait_for_state_change_or_timeout(
    hass: core.HomeAssistant, entity_id: str, timeout: float,
) -> None:
    """Wait for an entity to change state or timeout."""
    ev = asyncio.Event()

    @core.callback
    def _async_event_changed(event: Event[EventStateChangedData]) -> None:
        ev.set()

    unsub = async_track_state_change_event(hass, [entity_id], _async_event_changed)

    try:
        async with asyncio.timeout(STATE_CHANGE_TIMEOUT):
            await ev.wait()
    except TimeoutError:
        pass
    finally:
        unsub()
