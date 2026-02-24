"""Hue device representation for Emulated Hue +."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.const import STATE_ON
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ATTR_COLOR_TEMP_KELVIN,
)


@dataclass
class HueDevice:
    """Represents a virtual Hue device that maps to a Home Assistant entity."""
    
    hue_id: str
    name: str
    entity_id: str | None = None
    device_type: str = "light"
    created_at: str = ""
    modified_at: str = ""
    last_accessed_at: str | None = None
    last_accessed_by: str | None = None
    
    def __post_init__(self):
        """Set timestamps if not provided."""
        import datetime
        now = datetime.datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.modified_at:
            self.modified_at = now
    
    @property
    def is_linked(self) -> bool:
        """Check if device is linked to a Home Assistant entity."""
        return self.entity_id is not None
    
    @property
    def unique_id(self) -> str:
        """Return unique ID for this Hue device."""
        return f"ha_emulated_hue_{self.hue_id}"
    
    def record_access(self, client_ip: str) -> None:
        """Record an API access from a client."""
        import datetime
        self.last_accessed_at = datetime.datetime.now().isoformat()
        self.last_accessed_by = client_ip

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "hue_id": self.hue_id,
            "name": self.name,
            "entity_id": self.entity_id,
            "device_type": self.device_type,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "last_accessed_at": self.last_accessed_at,
            "last_accessed_by": self.last_accessed_by,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HueDevice":
        """Create from dictionary data."""
        return cls(
            hue_id=data["hue_id"],
            name=data["name"],
            entity_id=data.get("entity_id"),
            device_type=data.get("device_type", "light"),
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at", ""),
            last_accessed_at=data.get("last_accessed_at"),
            last_accessed_by=data.get("last_accessed_by"),
        )
    
    def get_hue_state(self, hass: HomeAssistant) -> dict[str, Any]:
        """Get current Hue-compatible state from Home Assistant entity."""
        if not self.entity_id:
            return {
                "on": False,
                "reachable": False,
                "bri": 0,
            }
        
        state = hass.states.get(self.entity_id)
        if not state:
            return {
                "on": False,
                "reachable": False,
                "bri": 0,
            }
        
        # Convert HA state to Hue format
        hue_state = {
            "on": state.state == STATE_ON,
            "reachable": True,
        }
        
        # Add brightness if available
        if ATTR_BRIGHTNESS in state.attributes:
            # Convert 0-255 to 1-254 (Hue range)
            brightness = state.attributes[ATTR_BRIGHTNESS]
            hue_state["bri"] = max(1, min(254, int(brightness)))
        elif state.state == STATE_ON:
            hue_state["bri"] = 254
        else:
            hue_state["bri"] = 0
        
        # Add color information if available
        if ATTR_HS_COLOR in state.attributes:
            h, s = state.attributes[ATTR_HS_COLOR]
            hue_state["hue"] = int(h / 360 * 65535)  # Convert to 0-65535
            hue_state["sat"] = int(s / 100 * 254)    # Convert to 0-254
            hue_state["colormode"] = "hs"
        elif ATTR_COLOR_TEMP_KELVIN in state.attributes:
            # HA 2024+: color_temp_kelvin is in Kelvin, convert to mireds
            kelvin = state.attributes[ATTR_COLOR_TEMP_KELVIN]
            hue_state["ct"] = max(153, min(500, int(1000000 / kelvin)))
            hue_state["colormode"] = "ct"
        elif "color_temp" in state.attributes:
            # Fallback: color_temp is in mireds (deprecated in HA 2026.1)
            mired = state.attributes["color_temp"]
            hue_state["ct"] = max(153, min(500, int(mired)))
            hue_state["colormode"] = "ct"
        
        return hue_state
    
    def update_name(self, new_name: str) -> None:
        """Update the device name."""
        import datetime
        self.name = new_name
        self.modified_at = datetime.datetime.now().isoformat()
    
    def update_entity_link(self, entity_id: str | None) -> None:
        """Update the linked Home Assistant entity."""
        import datetime
        self.entity_id = entity_id
        self.modified_at = datetime.datetime.now().isoformat()