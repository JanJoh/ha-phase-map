"""Repairs: confirm that a load has moved to another phase, or add a suggested meter."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_CHANNELS,
    CONF_CONNECTED,
    CONF_METER,
    CONF_NAME,
    CONF_PARENT,
    CONF_PHASES,
    CONF_PORTABLE,
    ISSUE_NEW_METER,
    NO_PARENT,
    SUBENTRY_LOAD,
)
from .discovery import SUPPORTED_UNITS, used_entities
from .engine import async_move_load

NO_PARENT_TEXT = {"en": "None: directly on the main fuse", "sv": "Ingen: direkt på huvudsäkringen"}
METER_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=["current", "power"])
)


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


class NewMeterFlow(RepairsFlow):
    """Add a meter that discovery found, or never suggest it again."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._entry_id = data.get("entry_id")
        self._key = data.get("key")

    # --- helpers -------------------------------------------------------------------

    def _entry(self):
        return self.hass.config_entries.async_get_entry(self._entry_id) if self._entry_id else None

    def _manager(self):
        entry = self._entry()
        engine = getattr(entry, "runtime_data", None) if entry else None
        return getattr(engine, "discovery", None)

    def _lang(self) -> str:
        return "sv" if (self.hass.config.language or "").startswith("sv") else "en"

    def _parents(self, entry) -> dict[str, list[str]]:
        """Metered loads a new meter can sit behind, with the phases each one measures."""
        out = {}
        for sub in entry.subentries.values():
            if sub.subentry_type != SUBENTRY_LOAD:
                continue
            phases = list(sub.data.get(CONF_CHANNELS) or {}) or list(sub.data.get(CONF_CONNECTED) or [])
            if (sub.data.get(CONF_CHANNELS) or sub.data.get(CONF_METER)) and phases:
                out[sub.subentry_id] = phases
        return out

    def _parent_selector(self, entry) -> selector.SelectSelector:
        options = [selector.SelectOptionDict(value=NO_PARENT, label=NO_PARENT_TEXT[self._lang()])]
        options += [selector.SelectOptionDict(value=sid, label=entry.subentries[sid].title)
                    for sid in self._parents(entry)]
        return selector.SelectSelector(
            selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
        )

    def _gone(self):
        manager = self._manager()
        cand = manager.candidate(self._key) if manager and self._key else None
        return manager, cand

    # --- steps ---------------------------------------------------------------------

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        manager, cand = self._gone()
        if cand is None:
            return self.async_abort(reason="candidate_gone")
        return self.async_show_menu(
            step_id="init",
            menu_options=["add", "ignore"],
            description_placeholders={"name": cand.get("name", ""), "details": manager.describe(cand, self._lang())},
        )

    async def async_step_ignore(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        manager, cand = self._gone()
        if manager is not None and self._key:
            manager.ignore(self._key)
        return self.async_create_entry(data={})

    async def async_step_add(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        manager, cand = self._gone()
        entry = self._entry()
        if cand is None or entry is None:
            return self.async_abort(reason="candidate_gone")
        phases: list[str] = list(entry.data[CONF_PHASES])
        parents = self._parents(entry)
        group = len(cand.get("channels", {})) > 1
        errors: dict[str, str] = {}
        if user_input is not None:
            parent = user_input.get(CONF_PARENT)
            parent = None if parent in (None, NO_PARENT) else parent
            if group:
                channels = {p: user_input[p] for p in phases if user_input.get(p)}
            else:
                channels = {user_input["phase"]: next(iter(cand["channels"].values()))}
            used = used_entities(self.hass)
            if not channels:
                errors["base"] = "no_meter"
            elif set(channels.values()) & used:
                return self.async_abort(reason="already_added")
            elif any(self._unit(e) not in SUPPORTED_UNITS for e in channels.values()):
                errors["base"] = "unsupported_unit"
            elif parent and not set(channels) <= set(parents.get(parent, [])):
                errors["base"] = "outside_parent"
            if not errors:
                data: dict[str, Any] = {CONF_NAME: user_input[CONF_NAME].strip() or cand["name"],
                                        CONF_CHANNELS: channels}
                if parent:
                    data[CONF_PARENT] = parent
                if not group and user_input.get(CONF_PORTABLE):
                    data[CONF_PORTABLE] = True
                self.hass.config_entries.async_add_subentry(
                    entry,
                    ConfigSubentry(data=MappingProxyType(data), subentry_type=SUBENTRY_LOAD,
                                   title=data[CONF_NAME], unique_id=None),
                )
                return self.async_create_entry(data={})
        parent_default = cand.get("parent") if cand.get("parent") in parents else NO_PARENT
        fields: dict[Any, Any] = {vol.Required(CONF_NAME, default=cand.get("name", "")): selector.TextSelector()}
        if group:
            mapping = cand.get("mapping", {})
            for p in phases:
                key = vol.Optional(p, default=mapping[p]) if mapping.get(p) else vol.Optional(p)
                fields[key] = METER_SELECTOR
        else:
            default = cand.get("phase") if cand.get("phase") in phases else phases[0]
            fields[vol.Required("phase", default=default)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=phases, mode=selector.SelectSelectorMode.LIST)
            )
        if parents:
            fields[vol.Required(CONF_PARENT, default=parent_default)] = self._parent_selector(entry)
        if not group:
            fields[vol.Optional(CONF_PORTABLE, default=False)] = selector.BooleanSelector()
        schema = vol.Schema(fields)
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="add_multi" if group else "add",
            data_schema=schema,
            errors=errors,
            description_placeholders={"name": cand.get("name", ""), "details": manager.describe(cand, self._lang())},
        )

    async def async_step_add_multi(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        return await self.async_step_add(user_input)

    def _unit(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return state.attributes.get("unit_of_measurement") if state else None


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    if issue_id.startswith(ISSUE_NEW_METER):
        return NewMeterFlow(dict(data or {}))
    return LoadMovedFlow(dict(data or {}))
