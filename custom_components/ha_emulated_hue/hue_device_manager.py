"""Manages Hue devices and ID assignments for Emulated Hue +."""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION, SUPPORTED_DOMAINS
from .hue_device import HueDevice

_LOGGER = logging.getLogger(__name__)


class _Sentinel(Enum):
    """Sentinel value for distinguishing 'not provided' from None."""

    UNSET = "unset"


class HueDeviceManager:
    """Manages virtual Hue devices and their ID assignments."""
    
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the device manager."""
        self.hass = hass
        self.config_entry = config_entry
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        
        # Device storage
        self._devices: dict[str, HueDevice] = {}
        self._retired_ids: set[str] = set()
        self._next_id_counter = 1
        
    async def async_setup(self) -> None:
        """Set up the device manager."""
        _LOGGER.info("Setting up Hue device manager")
        await self._load_data()
        
    async def async_cleanup(self) -> None:
        """Clean up the device manager."""
        _LOGGER.info("Cleaning up Hue device manager")
        await self._save_data()
        
    async def async_reload(self) -> None:
        """Reload the device manager."""
        _LOGGER.info("Reloading Hue device manager")
        await self._save_data()
        await self._load_data()
        
    async def _load_data(self) -> None:
        """Load device data from storage."""
        try:
            data = await self._store.async_load()
            if not data:
                _LOGGER.info("No existing device data found, starting fresh")
                return
                
            # Load devices
            devices_data = data.get("devices", {})
            for device_id, device_data in devices_data.items():
                self._devices[device_id] = HueDevice.from_dict(device_data)
                
            # Load retired IDs
            self._retired_ids = set(data.get("retired_ids", []))
            
            # Update ID counter
            all_ids = set(self._devices.keys()) | self._retired_ids
            if all_ids:
                max_id = max(int(id_str) for id_str in all_ids)
                self._next_id_counter = max_id + 1
                
            _LOGGER.info(
                "Loaded %d devices, %d retired IDs, next ID: %d",
                len(self._devices),
                len(self._retired_ids),
                self._next_id_counter
            )
            
        except Exception as err:
            _LOGGER.error("Failed to load device data: %s", err)
            
    async def _save_data(self) -> None:
        """Save device data to storage."""
        try:
            data = {
                "devices": {
                    device_id: device.to_dict()
                    for device_id, device in self._devices.items()
                },
                "retired_ids": list(self._retired_ids),
                "next_id_counter": self._next_id_counter,
            }
            await self._store.async_save(data)
            _LOGGER.debug("Saved device data successfully")
            
        except Exception as err:
            _LOGGER.error("Failed to save device data: %s", err)
            
    def _generate_hue_id(self) -> str:
        """Generate a new unique Hue ID."""
        while True:
            hue_id = str(self._next_id_counter)
            self._next_id_counter += 1
            
            # Ensure ID is not in use or retired
            if hue_id not in self._devices and hue_id not in self._retired_ids:
                return hue_id
                
    async def async_create_hue_device(
        self,
        name: str,
        entity_id: str | None = None,
    ) -> HueDevice:
        """Create a new Hue device."""
        hue_id = self._generate_hue_id()
        
        # Validate entity if provided
        if entity_id:
            if not self._is_valid_entity(entity_id):
                raise ValueError(f"Entity {entity_id} is not valid or not supported")
            if self._is_entity_already_linked(entity_id):
                raise ValueError(f"Entity {entity_id} is already linked to another Hue device")
        
        device = HueDevice(
            hue_id=hue_id,
            name=name,
            entity_id=entity_id,
            device_type=self._get_device_type(entity_id) if entity_id else "light"
        )
        
        self._devices[hue_id] = device
        await self._save_data()
        
        _LOGGER.info("Created Hue device: %s (ID: %s, Entity: %s)", name, hue_id, entity_id)
        return device
        
    async def async_delete_hue_device(self, hue_id: str) -> bool:
        """Delete a Hue device (retire its ID permanently)."""
        if hue_id not in self._devices:
            return False
            
        device = self._devices.pop(hue_id)
        self._retired_ids.add(hue_id)
        await self._save_data()
        
        _LOGGER.info("Deleted Hue device: %s (ID: %s - permanently retired)", device.name, hue_id)
        return True
        
    async def async_update_hue_device(
        self,
        hue_id: str,
        name: str | None = None,
        entity_id: str | None | _Sentinel = _Sentinel.UNSET,
    ) -> bool:
        """Update a Hue device.

        Args:
            hue_id: The Hue ID of the device to update.
            name: New name, or None to keep existing.
            entity_id: New entity ID, None to unlink, or _Sentinel.UNSET to keep.
        """
        if hue_id not in self._devices:
            return False

        device = self._devices[hue_id]

        # Validate new entity if provided (not UNSET and not None)
        if (
            not isinstance(entity_id, _Sentinel)
            and entity_id is not None
            and entity_id != device.entity_id
        ):
            if not self._is_valid_entity(entity_id):
                raise ValueError(f"Entity {entity_id} is not valid or not supported")
            if self._is_entity_already_linked(entity_id):
                raise ValueError(f"Entity {entity_id} is already linked to another Hue device")

        # Update fields
        if name:
            device.update_name(name)
        if not isinstance(entity_id, _Sentinel):  # None = unlink, str = re-link
            device.update_entity_link(entity_id)

        await self._save_data()
        
        _LOGGER.info("Updated Hue device: %s (ID: %s)", device.name, hue_id)
        return True
        
    def get_device(self, hue_id: str) -> HueDevice | None:
        """Get a device by Hue ID."""
        return self._devices.get(hue_id)
        
    def get_all_devices(self) -> list[HueDevice]:
        """Get all active devices."""
        return list(self._devices.values())
        
    def get_linked_devices(self) -> list[HueDevice]:
        """Get all devices linked to Home Assistant entities."""
        return [device for device in self._devices.values() if device.is_linked]
        
    def get_device_by_entity(self, entity_id: str) -> HueDevice | None:
        """Get device linked to a specific entity."""
        for device in self._devices.values():
            if device.entity_id == entity_id:
                return device
        return None
        
    def get_available_entities(self) -> list[str]:
        """Get list of available entities that can be linked."""
        all_entities = []
        
        for domain in SUPPORTED_DOMAINS:
            domain_entities = self.hass.states.async_entity_ids(domain)
            all_entities.extend(domain_entities)
            
        # Filter out already linked entities
        linked_entities = {device.entity_id for device in self._devices.values() if device.entity_id}
        return [entity for entity in all_entities if entity not in linked_entities]
        
    def _is_valid_entity(self, entity_id: str) -> bool:
        """Check if entity exists and is supported."""
        state = self.hass.states.get(entity_id)
        if not state:
            return False
            
        domain = entity_id.split(".")[0]
        return domain in SUPPORTED_DOMAINS
        
    def _is_entity_already_linked(self, entity_id: str) -> bool:
        """Check if entity is already linked to another device."""
        return self.get_device_by_entity(entity_id) is not None
        
    def _get_device_type(self, entity_id: str) -> str:
        """Get device type based on entity domain."""
        if not entity_id:
            return "light"
        return entity_id.split(".")[0]
        
    def get_stats(self) -> dict[str, Any]:
        """Get manager statistics."""
        return {
            "total_devices": len(self._devices),
            "linked_devices": len(self.get_linked_devices()),
            "retired_ids": len(self._retired_ids),
            "next_id": self._next_id_counter,
            "available_entities": len(self.get_available_entities()),
        }