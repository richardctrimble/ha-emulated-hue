"""Config flow for Emulated Hue + integration."""
import datetime
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_LISTEN_PORT,
    CONF_ADVERTISE_IP,
    CONF_ADVERTISE_PORT,
    DEFAULT_LISTEN_PORT,
    SUPPORTED_DOMAINS,
)

_LOGGER = logging.getLogger(__name__)


class HaEmulatedHueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Emulated Hue +."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        # Only allow a single instance of the integration
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            # Check for existing emulated_hue integration
            if self._check_existing_emulated_hue():
                errors["base"] = "existing_emulated_hue"
            else:
                return self.async_create_entry(
                    title="Emulated Hue +",
                    data=user_input,
                )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_LISTEN_PORT, default=DEFAULT_LISTEN_PORT
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(CONF_ADVERTISE_IP): str,
                vol.Optional(CONF_ADVERTISE_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    def _check_existing_emulated_hue(self) -> bool:
        """Check if the built-in emulated_hue integration is active."""
        for entry in self.hass.config_entries.async_entries():
            if entry.domain == "emulated_hue":
                return True
        return False

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Get the options flow for this handler."""
        return HaEmulatedHueOptionsFlow()


class HaEmulatedHueOptionsFlow(OptionsFlow):
    """Handle options flow for Emulated Hue +."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the main options menu with device overview."""
        device_manager = self._get_device_manager()
        devices = device_manager.get_all_devices()

        # Build device summary for the description
        # Note: description_placeholders are escaped as plain text by the
        # HA frontend, so markdown syntax must not be used here.
        if devices:
            lines: list[str] = []
            for device in sorted(devices, key=lambda d: int(d.hue_id)):
                entity_info = f" > {device.entity_id}" if device.entity_id else " (unlinked)"
                lines.append(f"{device.name} (ID {device.hue_id}){entity_info}")
                lines.append(f"    Last: {_format_last_access(device)}")
            device_list = "\n".join(lines)
        else:
            device_list = "No devices configured yet."

        return self.async_show_menu(
            step_id="init",
            menu_options=["add_device", "edit_device", "delete_device", "settings"],
            description_placeholders={"device_list": device_list},
        )

    # ------------------------------------------------------------------
    # Add device
    # ------------------------------------------------------------------

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a new Hue device."""
        errors: dict[str, str] = {}
        device_manager = self._get_device_manager()

        if user_input is not None:
            try:
                name = user_input["name"]
                entity_id = user_input.get("entity_id")

                await device_manager.async_create_hue_device(name, entity_id)

                return self.async_create_entry(
                    title="", data=self.config_entry.options
                )

            except ValueError as err:
                error_msg = str(err)
                if "already linked" in error_msg:
                    errors["base"] = "entity_already_linked"
                elif "not valid" in error_msg:
                    errors["base"] = "entity_invalid"
                else:
                    errors["base"] = "create_failed"

        data_schema = vol.Schema(
            {
                vol.Required("name"): str,
                vol.Optional("entity_id"): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=SUPPORTED_DOMAINS,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="add_device",
            data_schema=data_schema,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Edit device  (two-step: select device -> edit details)
    # ------------------------------------------------------------------

    async def async_step_edit_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: select which device to edit."""
        device_manager = self._get_device_manager()
        devices = device_manager.get_all_devices()

        if not devices:
            return self.async_abort(reason="no_devices")

        if user_input is not None:
            self._editing_device_id = user_input["device_id"]
            return await self.async_step_edit_device_details()

        device_options = [
            {
                "value": d.hue_id,
                "label": f"{d.name} (ID {d.hue_id})",
            }
            for d in sorted(devices, key=lambda d: int(d.hue_id))
        ]

        return self.async_show_form(
            step_id="edit_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=device_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_edit_device_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: edit the selected device's name / entity link."""
        errors: dict[str, str] = {}
        device_manager = self._get_device_manager()

        hue_id: str | None = getattr(self, "_editing_device_id", None)
        if not hue_id:
            return self.async_abort(reason="unknown_error")

        device = device_manager.get_device(hue_id)
        if not device:
            return self.async_abort(reason="device_not_found")

        if user_input is not None:
            try:
                name = user_input.get("name", device.name)
                entity_id = user_input.get("entity_id")

                success = await device_manager.async_update_hue_device(
                    hue_id, name, entity_id
                )
                if success:
                    return self.async_create_entry(
                        title="", data=self.config_entry.options
                    )
                errors["base"] = "update_failed"

            except ValueError as err:
                error_msg = str(err)
                if "already linked" in error_msg:
                    errors["base"] = "entity_already_linked"
                elif "not valid" in error_msg:
                    errors["base"] = "entity_invalid"
                else:
                    errors["base"] = "update_failed"

        # Build schema with current values as defaults
        schema_dict: dict[vol.Marker, Any] = {
            vol.Required("name", default=device.name): str,
        }
        if device.entity_id:
            schema_dict[
                vol.Optional("entity_id", default=device.entity_id)
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=SUPPORTED_DOMAINS,
                )
            )
        else:
            schema_dict[
                vol.Optional("entity_id")
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=SUPPORTED_DOMAINS,
                )
            )

        return self.async_show_form(
            step_id="edit_device_details",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "hue_id": hue_id,
                "device_name": device.name,
                "last_access": _format_last_access(device),
            },
        )

    # ------------------------------------------------------------------
    # Delete device
    # ------------------------------------------------------------------

    async def async_step_delete_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete (permanently retire) a device."""
        device_manager = self._get_device_manager()
        devices = device_manager.get_all_devices()

        if not devices:
            return self.async_abort(reason="no_devices")

        if user_input is not None:
            hue_id = user_input["device_id"]
            success = await device_manager.async_delete_hue_device(hue_id)

            if success:
                return self.async_create_entry(
                    title="", data=self.config_entry.options
                )
            return self.async_abort(reason="device_not_found")

        device_options = [
            {
                "value": d.hue_id,
                "label": f"{d.name} (ID {d.hue_id})",
            }
            for d in sorted(devices, key=lambda d: int(d.hue_id))
        ]

        return self.async_show_form(
            step_id="delete_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=device_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration network settings."""
        if user_input is not None:
            cleaned: dict[str, Any] = {}
            for key, val in user_input.items():
                if val is not None and val != "":
                    cleaned[key] = val

            new_data = dict(self.config_entry.data)
            new_data.update(cleaned)

            for key in (CONF_ADVERTISE_IP, CONF_ADVERTISE_PORT):
                if key not in cleaned:
                    new_data.pop(key, None)

            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            return self.async_create_entry(
                title="", data=self.config_entry.options
            )

        current_data = self.config_entry.data

        schema_dict: dict[vol.Marker, Any] = {
            vol.Required(
                CONF_LISTEN_PORT,
                default=current_data.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        }

        advertise_ip = current_data.get(CONF_ADVERTISE_IP)
        if advertise_ip:
            schema_dict[vol.Optional(CONF_ADVERTISE_IP, default=advertise_ip)] = str
        else:
            schema_dict[vol.Optional(CONF_ADVERTISE_IP)] = str

        advertise_port = current_data.get(CONF_ADVERTISE_PORT)
        if advertise_port:
            schema_dict[
                vol.Optional(CONF_ADVERTISE_PORT, default=advertise_port)
            ] = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))
        else:
            schema_dict[vol.Optional(CONF_ADVERTISE_PORT)] = vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            )

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(schema_dict),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_device_manager(self):
        """Get the device manager for this config entry."""
        return self.hass.data[DOMAIN][self.config_entry.entry_id]["device_manager"]


def _format_last_access(device) -> str:
    """Format the last-access info for a device as a human-readable string."""
    if not device.last_accessed_at:
        return "never"

    try:
        accessed = datetime.datetime.fromisoformat(device.last_accessed_at)
        delta = datetime.datetime.now() - accessed
        total_seconds = int(delta.total_seconds())

        if total_seconds < 60:
            age = f"{total_seconds}s ago"
        elif total_seconds < 3600:
            age = f"{total_seconds // 60} min ago"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            age = f"{hours}h ago"
        else:
            days = total_seconds // 86400
            age = f"{days}d ago"

        client = device.last_accessed_by or "unknown"
        return f"{age} from {client}"

    except (ValueError, TypeError):
        return "unknown"
