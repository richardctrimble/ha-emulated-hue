"""Emulated Hue + integration for Home Assistant.

Runs a standalone HTTP server + SSDP responder that emulates a Philips Hue
bridge, allowing Alexa (and other Hue clients) to discover and control
Home Assistant entities via the UI-managed device list.
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import KEY_HASS
from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ADVERTISE_IP,
    CONF_ADVERTISE_PORT,
    CONF_LISTEN_PORT,
    DEFAULT_LISTEN_PORT,
    DOMAIN,
    SERVICE_RELOAD,
    SERVICE_TEST_CREATE_DEVICE,
    SERVICE_TEST_LIST_DEVICES,
)
from .hue_api import (
    KEY_ADVERTISE_IP,
    KEY_ADVERTISE_PORT,
    KEY_CACHED_STATES,
    KEY_DEVICE_MANAGER,
    HueAllGroupsStateView,
    HueAllLightsStateView,
    HueConfigView,
    HueFullStateView,
    HueGroupView,
    HueOneLightChangeView,
    HueOneLightStateView,
    HueUnauthorizedUser,
    HueUsernameView,
)
from .hue_device_manager import HueDeviceManager
from .upnp import DescriptionXmlView, async_create_upnp_datagram_endpoint

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Emulated Hue + component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Emulated Hue + from a config entry."""
    _LOGGER.info("Setting up Emulated Hue + integration")

    # Initialize device manager
    device_manager = HueDeviceManager(hass, entry)
    await device_manager.async_setup()

    # Resolve network addresses
    local_ip = await async_get_source_ip(hass)
    listen_port: int = entry.data.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT)
    advertise_ip: str = entry.data.get(CONF_ADVERTISE_IP) or local_ip
    advertise_port: int = entry.data.get(CONF_ADVERTISE_PORT) or listen_port

    # Store in hass data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "device_manager": device_manager,
    }

    # Build the standalone aiohttp web application for the Hue API
    app = web.Application()
    app[KEY_HASS] = hass
    app[KEY_DEVICE_MANAGER] = device_manager
    app[KEY_CACHED_STATES] = {}
    app[KEY_ADVERTISE_IP] = advertise_ip
    app[KEY_ADVERTISE_PORT] = advertise_port

    # Freeze on_startup so we can call startup() outside the normal flow
    app._on_startup.freeze()  # noqa: SLF001
    await app.startup()

    # Register all Hue API routes
    DescriptionXmlView(advertise_ip, advertise_port).register(hass, app, app.router)
    HueUsernameView().register(hass, app, app.router)
    HueConfigView().register(hass, app, app.router)
    HueUnauthorizedUser().register(hass, app, app.router)
    HueAllLightsStateView().register(hass, app, app.router)
    HueOneLightStateView().register(hass, app, app.router)
    HueOneLightChangeView().register(hass, app, app.router)
    HueAllGroupsStateView().register(hass, app, app.router)
    HueGroupView().register(hass, app, app.router)
    HueFullStateView().register(hass, app, app.router)

    async def _start_bridge(event: Event) -> None:
        """Start the HTTP server and SSDP responder after HA is fully started."""
        _LOGGER.info(
            "Starting Emulated Hue bridge on %s:%s (advertising %s:%s)",
            local_ip,
            listen_port,
            advertise_ip,
            advertise_port,
        )

        # Start SSDP/UPnP responder
        try:
            protocol = await async_create_upnp_datagram_endpoint(
                local_ip,
                True,  # upnp_bind_multicast
                advertise_ip,
                advertise_port,
            )
        except OSError as error:
            _LOGGER.error("Failed to create SSDP responder: %s", error)
            protocol = None

        # Start HTTP server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, local_ip, listen_port)

        try:
            await site.start()
        except OSError as error:
            _LOGGER.error(
                "Failed to start HTTP server on port %d: %s", listen_port, error
            )
            if protocol:
                protocol.close()
            return

        _LOGGER.info("Emulated Hue bridge is running")

        async def _stop_bridge(event: Event) -> None:
            """Stop the HTTP server and SSDP responder."""
            _LOGGER.info("Stopping Emulated Hue bridge")
            if protocol:
                protocol.close()
            await site.stop()
            await runner.cleanup()

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_bridge)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start_bridge)

    # Register development/testing services
    if not hass.services.has_service(DOMAIN, SERVICE_RELOAD):
        await _async_register_services(hass, device_manager)

    _LOGGER.info("Emulated Hue + setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Emulated Hue + integration")

    # Clean up device manager
    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        device_manager = hass.data[DOMAIN][entry.entry_id]["device_manager"]
        await device_manager.async_cleanup()

    # Remove from hass data
    if DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)

        # Remove services if this was the last entry
        if not hass.data[DOMAIN]:
            for service in (SERVICE_RELOAD, SERVICE_TEST_CREATE_DEVICE, SERVICE_TEST_LIST_DEVICES):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)

    return True


async def _async_register_services(
    hass: HomeAssistant, device_manager: HueDeviceManager
) -> None:
    """Register development and testing services."""

    async def reload_service(call: ServiceCall) -> None:
        """Reload the integration."""
        _LOGGER.info("Reloading Emulated Hue + integration")
        await device_manager.async_reload()

    async def create_device_service(call: ServiceCall) -> None:
        """Create a test device."""
        name = call.data.get("name", "Test Device")
        entity_id = call.data.get("entity_id")

        device = await device_manager.async_create_hue_device(name, entity_id)
        _LOGGER.info("Created test device: %s (ID: %s)", device.name, device.hue_id)

    async def list_devices_service(call: ServiceCall) -> None:
        """List all current devices."""
        devices = device_manager.get_all_devices()
        _LOGGER.info(
            "Current devices: %s",
            [
                f"{d.name} (ID: {d.hue_id}, Entity: {d.entity_id})"
                for d in devices
            ],
        )

    hass.services.async_register(DOMAIN, SERVICE_RELOAD, reload_service)
    hass.services.async_register(DOMAIN, SERVICE_TEST_CREATE_DEVICE, create_device_service)
    hass.services.async_register(DOMAIN, SERVICE_TEST_LIST_DEVICES, list_devices_service)
