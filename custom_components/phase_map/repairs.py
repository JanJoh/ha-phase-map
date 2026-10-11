"""Repairs: confirm that a load has moved to another phase."""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .engine import async_move_load


class LoadMovedFlow(RepairsFlow):
    """Move the load to the phase the switch events point at."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        if user_input is not None:
            moved = await async_move_load(self.hass, self._data["entry_id"], self._data["subentry_id"],
                                          self._data["to"])
            if not moved:
                return self.async_abort(reason="load_gone")
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={k: str(self._data.get(k, "")) for k in ("name", "from", "to")},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    return LoadMovedFlow(dict(data or {}))
