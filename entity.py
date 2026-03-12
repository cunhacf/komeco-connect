"""Base entity classes."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KomecoDataUpdateCoordinator


class KomecoEntity(CoordinatorEntity[KomecoDataUpdateCoordinator]):
    """Shared base for Komeco entities."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device = self.coordinator.data.get("device", {})
        device_name = device.get("deviceName") or f"Komeco {self.coordinator.api.device_id}"
        manufacturer = device.get("manufacturer") or "Komeco"
        model = device.get("deviceModel", {}).get("modelNumber") or device.get("modelNumber") or "Komeco Device"
        sw_version = device.get("version")
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.api.device_id or "unknown")},
            name=device_name,
            manufacturer=manufacturer,
            model=model,
            sw_version=sw_version,
        )
