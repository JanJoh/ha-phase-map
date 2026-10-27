"""Config flow: the main fuse as the config entry, loads and detectors as subentries."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_CHANNELS,
    CONF_CONNECTED,
    CONF_DETECT_PHASES,
    CONF_DISCOVERY,
    CONF_DISCOVERY_CONFIDENCE,
    CONF_DRAW,
    CONF_ESTIMATED_PHASES,
    CONF_ESTIMATED_W,
    CONF_FUSE_A,
    CONF_HOLD_CURRENT_A,
    CONF_INFO_ENTITY,
    CONF_LABEL_NAME,
    CONF_LABELS,
    CONF_MARGIN_A,
    CONF_METER,
    CONF_MIN_CURRENT_A,
    CONF_NAME,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_OVERLOAD_DELAY,
    CONF_PARENT,
    CONF_PHASE_COUNT,
    CONF_PHASES,
    CONF_PORTABLE,
    CONF_SOURCES,
    CONF_VOLTAGE,
    DEFAULT_DISCOVERY,
    DEFAULT_DISCOVERY_CONFIDENCE,
    DEFAULT_FUSE_A,
    DEFAULT_LABEL_NAME,
    DEFAULT_MARGIN_A,
    DEFAULT_MIN_CURRENT_A,
    DEFAULT_OFF_DELAY,
    DEFAULT_ON_DELAY,
    DEFAULT_OVERLOAD_DELAY,
    DEFAULT_VOLTAGE,
    DOMAIN,
    DRAW_EQUAL,
    NO_PARENT,
    PHASE_NAMES,
    SUBENTRY_DETECTOR,
    SUBENTRY_LOAD,
    phase_value_key,
)
from .detection import async_suggest_phase
from .energy_prefs import async_energy_devices, suggest_parent
from .hierarchy import descendants
from .labels import async_forget_labels, async_label_counts, async_remove_labels

SECTION_LABELS = "phase_labels"
SECTION_DISCOVERY = "discovery_settings"
DRAW_PHASE = "phase"               # form value: all on the phase picked above
SUPPORTED_UNITS = {"A", "mA", "W", "kW"}
NO_PARENT_TEXT = {"en": "None: directly on the main fuse", "sv": "Ingen: direkt på huvudsäkringen"}
CONFIDENCE_TEXT = {"en": {"high": "high", "medium": "medium"}, "sv": {"high": "hög", "medium": "medel"}}

METER_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=["current", "power"])
)
# A load with a meter per phase: the sensor the card opens on a click (e.g. the meter's total power)
INFO_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _number(minimum: float, maximum: float, step: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=minimum, max=maximum, step=step, unit_of_measurement=unit,
                                      mode=selector.NumberSelectorMode.BOX)
    )


def _phase_select(phases: list[str], multiple: bool) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=phases, multiple=multiple, mode=selector.SelectSelectorMode.LIST)
    )


def _unit_ok(hass, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    return state is not None and state.attributes.get("unit_of_measurement") in SUPPORTED_UNITS


def _fuse_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default="Main fuse"): selector.TextSelector(),
            vol.Required(CONF_PHASE_COUNT, default="3"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["1", "2", "3"], translation_key="phase_count",
                                              mode=selector.SelectSelectorMode.LIST)
            ),
            vol.Required(CONF_VOLTAGE, default=DEFAULT_VOLTAGE): _number(100, 400, 1, "V"),
            vol.Required(CONF_FUSE_A, default=DEFAULT_FUSE_A): _number(6, 250, 1, "A"),
        }
    )


def _sources_schema(phases: list[str]) -> vol.Schema:
    return vol.Schema({vol.Required(p): METER_SELECTOR for p in phases})


class PhaseMapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a main fuse."""

    VERSION = 1

    def __init__(self) -> None:
        self._fuse: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PhaseMapOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_LOAD: LoadSubentryFlow, SUBENTRY_DETECTOR: DetectorSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._fuse = user_input
            return await self.async_step_sources()
        return self.async_show_form(step_id="user", data_schema=_fuse_schema())

    async def async_step_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        phases = PHASE_NAMES[int(self._fuse[CONF_PHASE_COUNT])]
        errors: dict[str, str] = {}
        if user_input is not None:
            for p in phases:
                if not _unit_ok(self.hass, user_input[p]):
                    errors[p] = "unsupported_unit"
            if len(set(user_input.values())) != len(phases):
                errors["base"] = "same_sensor_twice"
            if not errors:
                await self.async_set_unique_id(user_input[phases[0]])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._fuse[CONF_NAME],
                    data={
                        CONF_PHASES: phases,
                        CONF_VOLTAGE: float(self._fuse[CONF_VOLTAGE]),
                        CONF_FUSE_A: float(self._fuse[CONF_FUSE_A]),
                        CONF_SOURCES: {p: user_input[p] for p in phases},
                    },
                )
        return self.async_show_form(
            step_id="sources",
            data_schema=self.add_suggested_values_to_schema(_sources_schema(phases), user_input or {}),
            errors=errors,
            description_placeholders={"phases": ", ".join(phases)},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change fuse size, voltage or the main-fuse sensors."""
        entry = self._get_reconfigure_entry()
        phases = entry.data[CONF_PHASES]
        schema = vol.Schema(
            {
                vol.Required(CONF_VOLTAGE): _number(100, 400, 1, "V"),
                vol.Required(CONF_FUSE_A): _number(6, 250, 1, "A"),
                **{vol.Required(p): METER_SELECTOR for p in phases},
            }
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            for p in phases:
                if not _unit_ok(self.hass, user_input[p]):
                    errors[p] = "unsupported_unit"
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_VOLTAGE: float(user_input[CONF_VOLTAGE]),
                        CONF_FUSE_A: float(user_input[CONF_FUSE_A]),
                        CONF_SOURCES: {p: user_input[p] for p in phases},
                    },
                )
        current = {CONF_VOLTAGE: entry.data[CONF_VOLTAGE], CONF_FUSE_A: entry.data[CONF_FUSE_A],
                   **entry.data[CONF_SOURCES]}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
        )


class PhaseMapOptionsFlow(OptionsFlow):
    """Settings (overload warning, phase labels, meter discovery) and the suggested meters."""

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}

    def _discovery(self):
        engine = getattr(self.config_entry, "runtime_data", None)
        return getattr(engine, "discovery", None)

    def _lang(self) -> str:
        return "sv" if (self.hass.config.language or "").startswith("sv") else "en"

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "energy_seed", "suggestions", "ignored"])

    async def async_step_energy_seed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Suggest the Energy dashboard's individual devices, also while discovery is off."""
        manager = self._discovery()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            manager.request("energy")
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(step_id="energy_seed", data_schema=vol.Schema({}))

    async def async_step_suggestions(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """What discovery found, with a way to ignore some and to scan again."""
        manager = self._discovery()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        lang = self._lang()
        cands = manager.data.get("candidates", {})
        listed = {k: c for k, c in cands.items() if k not in manager.data.get("ignored", {})}
        if user_input is not None:
            for key in user_input.get("ignore", []):
                manager.ignore(key)
            if user_input.get("scan_now"):
                manager.request("scan")
            return self.async_create_entry(data=dict(self.config_entry.options))
        status_text = {
            "en": {"offer": "suggested", "unclear": "could not place", "no_data": "no statistics yet"},
            "sv": {"offer": "föreslås", "unclear": "kunde inte placeras", "no_data": "ingen statistik ännu"},
        }[lang]
        lines = []
        for c in sorted(listed.values(), key=lambda c: (c.get("status") != "offer", c.get("name", ""))):
            line = f"- **{c.get('name')}**: {status_text.get(c.get('status'), c.get('status'))}"
            if c.get("status") == "offer":
                line += f". {manager.describe(c, lang)}"
            lines.append(line)
        empty = "Inga mätare hittade ännu." if lang == "sv" else "No meters found yet."
        options = [selector.SelectOptionDict(value=k, label=c.get("name", k)) for k, c in listed.items()]
        fields: dict[Any, Any] = {}
        if options:
            fields[vol.Optional("ignore", default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, multiple=True, mode=selector.SelectSelectorMode.LIST)
            )
        if manager.enabled:
            fields[vol.Optional("scan_now", default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="suggestions",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "candidates": "\n".join(lines) or empty,
                "last_scan": manager.data.get("last_scan") or "–",
            },
        )

    async def async_step_ignored(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ignored meters: untick to have them suggested again."""
        manager = self._discovery()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        items = {k: v.get("name", k) for k, v in manager.data.get("ignored", {}).items()}
        items.update({e: e for e in manager.data.get("ignored_entities", {})})
        if user_input is not None:
            keep = set(user_input.get("ignored", []))
            for key in set(items) - keep:
                manager.unignore(key)
            return self.async_create_entry(data=dict(self.config_entry.options))
        options = [selector.SelectOptionDict(value=k, label=name) for k, name in items.items()]
        fields = {}
        if options:
            fields[vol.Optional("ignored", default=list(items))] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, multiple=True, mode=selector.SelectSelectorMode.LIST)
            )
        return self.async_show_form(step_id="ignored", data_schema=vol.Schema(fields),
                                    description_placeholders={"count": str(len(items))})

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        opts = self.config_entry.options
        phases = self.config_entry.data[CONF_PHASES]
        errors: dict[str, str] = {}
        if user_input is not None:
            labels_in = user_input.pop(SECTION_LABELS, {})
            discovery_in = user_input.pop(SECTION_DISCOVERY, {})
            data = {**user_input, CONF_LABELS: bool(labels_in.get(CONF_LABELS)),
                    CONF_LABEL_NAME: (labels_in.get(CONF_LABEL_NAME) or DEFAULT_LABEL_NAME).strip(),
                    CONF_DISCOVERY: bool(discovery_in.get(CONF_DISCOVERY, DEFAULT_DISCOVERY)),
                    CONF_DISCOVERY_CONFIDENCE: discovery_in.get(CONF_DISCOVERY_CONFIDENCE,
                                                                DEFAULT_DISCOVERY_CONFIDENCE)}
            values = {p: str(labels_in.get(phase_value_key(p)) or "").strip() for p in phases}
            if any(not v for v in values.values()):
                errors["base"] = "empty_value"
            elif len(set(values.values())) != len(values):
                errors["base"] = "same_value_twice"
            data.update({phase_value_key(p): v for p, v in values.items()})
            if not errors:
                if opts.get(CONF_LABELS) and not data[CONF_LABELS]:
                    added, _ = await async_label_counts(self.hass, self.config_entry.entry_id)
                    if added:
                        self._pending = data
                        return await self.async_step_disable_labels()
                return self.async_create_entry(data=data)
        current = {**opts, **(user_input or {})}
        label_schema = vol.Schema(
            {
                vol.Required(CONF_LABELS, default=bool(opts.get(CONF_LABELS))): selector.BooleanSelector(),
                vol.Required(CONF_LABEL_NAME, default=opts.get(CONF_LABEL_NAME, DEFAULT_LABEL_NAME)):
                    selector.TextSelector(),
                **{
                    vol.Required(phase_value_key(p), default=opts.get(phase_value_key(p), str(i + 1))):
                        selector.TextSelector()
                    for i, p in enumerate(phases)
                },
            }
        )
        discovery_schema = vol.Schema(
            {
                vol.Required(CONF_DISCOVERY, default=bool(opts.get(CONF_DISCOVERY, DEFAULT_DISCOVERY))):
                    selector.BooleanSelector(),
                vol.Required(CONF_DISCOVERY_CONFIDENCE,
                             default=opts.get(CONF_DISCOVERY_CONFIDENCE, DEFAULT_DISCOVERY_CONFIDENCE)):
                    selector.SelectSelector(selector.SelectSelectorConfig(
                        options=["high", "medium"], translation_key="discovery_confidence",
                        mode=selector.SelectSelectorMode.LIST)),
            }
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_MARGIN_A, default=current.get(CONF_MARGIN_A, DEFAULT_MARGIN_A)): _number(0, 50, 0.5, "A"),
                vol.Required(CONF_OVERLOAD_DELAY, default=current.get(CONF_OVERLOAD_DELAY, DEFAULT_OVERLOAD_DELAY)):
                    _number(0, 3600, 5, "s"),
                vol.Required(SECTION_LABELS): section(label_schema, {"collapsed": not opts.get(CONF_LABELS)}),
                vol.Optional(SECTION_DISCOVERY, default={}): section(discovery_schema,
                                                         {"collapsed": not opts.get(CONF_DISCOVERY, DEFAULT_DISCOVERY)}),
            }
        )
        return self.async_show_form(
            step_id="settings",
            data_schema=schema,
            errors=errors,
            description_placeholders={"phases": ", ".join(phases)},
        )

    async def async_step_disable_labels(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Turning labels off: remove the ones Phase Map added, or keep them?"""
        added, devices = await async_label_counts(self.hass, self.config_entry.entry_id)
        return self.async_show_menu(
            step_id="disable_labels",
            menu_options=["remove_labels", "keep_labels"],
            description_placeholders={"labels": str(added), "devices": str(devices)},
        )

    async def async_step_remove_labels(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await async_remove_labels(self.hass, self.config_entry.entry_id)
        return self.async_create_entry(data=self._pending)

    async def async_step_keep_labels(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await async_forget_labels(self.hass, self.config_entry.entry_id)
        return self.async_create_entry(data=self._pending)


class LoadSubentryFlow(ConfigSubentryFlow):
    """Add or edit a load: one meter, a multi-phase meter, or an estimated unmetered load.

    After the details, a load can be placed behind another meter (its parent), for
    example a CEE outlet behind the garage's incoming meter. The parent is pre-selected
    from the Energy dashboard's "upstream device" when it has one.
    """

    def __init__(self) -> None:
        self._kind = ""
        self._data: dict[str, Any] = {}
        self._parent: str | None = None
        self._suggestion = None

    @property
    def _phases(self) -> list[str]:
        return self._get_entry().data[CONF_PHASES]

    # --- parents -------------------------------------------------------------------

    def _load_subentries(self) -> list[ConfigSubentry]:
        return [s for s in self._get_entry().subentries.values() if s.subentry_type == SUBENTRY_LOAD]

    def _parent_candidates(self, exclude: str | None = None) -> list[ConfigSubentry]:
        """Metered loads that could hold this one: not itself, and not one of its descendants."""
        subs = self._load_subentries()
        blocked: set[str] = set()
        if exclude:
            blocked = {exclude} | descendants(
                [_SubLoad(s) for s in subs], exclude
            )
        return [s for s in subs if (s.data.get(CONF_CHANNELS) or s.data.get(CONF_METER)) and s.subentry_id not in blocked]

    def _parent_selector(self, candidates: list[ConfigSubentry]) -> selector.SelectSelector:
        lang = "sv" if (self.hass.config.language or "").startswith("sv") else "en"
        options = [selector.SelectOptionDict(value=NO_PARENT, label=NO_PARENT_TEXT[lang])]
        options += [selector.SelectOptionDict(value=s.subentry_id, label=s.title) for s in candidates]
        return selector.SelectSelector(
            selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
        )

    def _parent_channels(self, parent: str | None) -> dict[str, str]:
        sub = self._get_entry().subentries.get(parent or "")
        return dict(sub.data.get(CONF_CHANNELS, {})) if sub else {}

    def _parent_phases(self, parent: str | None) -> list[str]:
        sub = self._get_entry().subentries.get(parent or "")
        if sub is None:
            return []
        return list(sub.data.get(CONF_CHANNELS) or {}) or list(sub.data.get(CONF_CONNECTED) or [])

    def _outside_parent(self, phases: list[str], parent: str | None) -> bool:
        return bool(parent) and not set(phases) <= set(self._parent_phases(parent))

    async def _energy_parent(self, meters: list[str], candidates: list[ConfigSubentry]) -> str | None:
        if not meters or not candidates:
            return None
        devices = await async_energy_devices(self.hass)
        return suggest_parent(
            self.hass,
            devices,
            meters,
            {s.subentry_id: [*s.data.get(CONF_CHANNELS, {}).values(), *([s.data[CONF_METER]] if s.data.get(CONF_METER) else [])]
             for s in candidates},
            self._get_entry().data[CONF_SOURCES].values(),
        )

    async def _after_details(self) -> SubentryFlowResult:
        if self._parent_candidates():
            return await self.async_step_parent()
        return await self._finish()

    async def async_step_parent(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Is this load measured by another meter as well (garage, sub-panel)?"""
        candidates = self._parent_candidates()
        errors: dict[str, str] = {}
        if user_input is not None:
            parent = user_input.get(CONF_PARENT)
            parent = None if parent in (None, NO_PARENT) else parent
            if self._kind != "meter" and self._outside_parent(self._phases_of(self._data), parent):
                errors[CONF_PARENT] = "outside_parent"
            else:
                self._parent = parent
                return await self._finish()
        default = await self._energy_parent(list(self._data.get(CONF_CHANNELS, {}).values()), candidates)
        schema = vol.Schema(
            {vol.Required(CONF_PARENT, default=default or NO_PARENT): self._parent_selector(candidates)}
        )
        return self.async_show_form(
            step_id="parent",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders={"name": self._data[CONF_NAME]},
        )

    @staticmethod
    def _phases_of(data: dict[str, Any]) -> list[str]:
        return list(data.get(CONF_CHANNELS) or data.get(CONF_ESTIMATED_PHASES) or [])

    async def _finish(self) -> SubentryFlowResult:
        if self._kind == "meter":
            meter = next(iter(self._data[CONF_CHANNELS].values()))
            entry = self._get_entry()
            # Behind a parent, compare with the parent's channels: same phases, far less noise.
            sources = self._parent_channels(self._parent) or entry.data[CONF_SOURCES]
            self._suggestion = await async_suggest_phase(self.hass, meter, sources, float(entry.data[CONF_VOLTAGE]))
            return await self.async_step_phase()
        return self._create(self._data)

    def _create(self, data: dict[str, Any]) -> SubentryFlowResult:
        data = dict(data)
        if self._parent:
            data[CONF_PARENT] = self._parent
        return self.async_create_entry(title=data[CONF_NAME], data=data)

    # --- kinds of load -------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["meter", "multi", "estimated"])

    # One meter on one phase, with a suggested phase from history
    async def async_step_meter(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not _unit_ok(self.hass, user_input["entity"]):
                errors["entity"] = "unsupported_unit"
            else:
                self._kind = "meter"
                # The phase key is a placeholder until the phase step.
                self._data = {CONF_NAME: user_input[CONF_NAME], CONF_CHANNELS: {"?": user_input["entity"]}}
                return await self._after_details()
        schema = vol.Schema({vol.Required(CONF_NAME): selector.TextSelector(), vol.Required("entity"): METER_SELECTOR})
        return self.async_show_form(step_id="meter", data_schema=schema, errors=errors)

    async def async_step_phase(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Pick the phase; pre-selected from history when the analysis was clear."""
        name = self._data[CONF_NAME]
        meter = next(iter(self._data[CONF_CHANNELS].values()))
        options = self._parent_phases(self._parent) or self._phases
        if user_input is not None:
            if self._outside_parent(_connected(user_input, self._phases), self._parent):
                errors = {CONF_CONNECTED: "outside_parent"}
            else:
                return self._create(_one_meter_data(name, meter, user_input, self._phases))
        else:
            errors = {}
        sug = self._suggestion
        found = bool(sug and sug.phase and sug.phase in options)
        default = sug.phase if found else options[0]
        schema = vol.Schema(
            {
                vol.Required("phase", default=default): _phase_select(options, multiple=False),
                **_multi_fields(options, DRAW_PHASE if found else DRAW_EQUAL),
                vol.Optional(CONF_PORTABLE, default=False): selector.BooleanSelector(),
            }
        )
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        if found:
            score = sug.scores[sug.phase]
            lang = "sv" if (self.hass.config.language or "").startswith("sv") else "en"
            placeholders = {
                "name": name,
                "phase": sug.phase,
                "confidence": CONFIDENCE_TEXT[lang][sug.confidence],
                "steps": str(sug.steps),
                "gain": f"{score.gain:.2f}" if score.gain is not None else "–",
            }
            return self.async_show_form(step_id="phase", data_schema=schema, description_placeholders=placeholders,
                                        errors=errors)
        return self.async_show_form(
            step_id="phase_manual",
            data_schema=schema,
            description_placeholders={"name": name, "steps": str(sug.steps if sug else 0)},
            errors=errors,
        )

    async def async_step_phase_manual(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self.async_step_phase(user_input)

    # A multi-phase load with one meter (channel) per phase
    async def async_step_multi(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            channels = {p: user_input[p] for p in self._phases if user_input.get(p)}
            if not channels:
                errors["base"] = "no_meter"
            for p, e in channels.items():
                if not _unit_ok(self.hass, e):
                    errors[p] = "unsupported_unit"
            if not errors:
                self._kind = "multi"
                self._data = {CONF_NAME: user_input[CONF_NAME], CONF_CHANNELS: channels}
                if user_input.get(CONF_INFO_ENTITY) and len(channels) > 1:
                    self._data[CONF_INFO_ENTITY] = user_input[CONF_INFO_ENTITY]
                return await self._after_details()
        schema = vol.Schema(
            {vol.Required(CONF_NAME): selector.TextSelector(), **{vol.Optional(p): METER_SELECTOR for p in self._phases},
             vol.Optional(CONF_INFO_ENTITY): INFO_SELECTOR}
        )
        return self.async_show_form(step_id="multi", data_schema=schema, errors=errors)

    # A load without a meter, drawn from an estimate (e.g. a sauna)
    async def async_step_estimated(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_ESTIMATED_PHASES):
                errors[CONF_ESTIMATED_PHASES] = "no_phase"
            else:
                self._kind = "estimated"
                self._data = {CONF_NAME: user_input[CONF_NAME],
                              CONF_ESTIMATED_PHASES: user_input[CONF_ESTIMATED_PHASES],
                              CONF_ESTIMATED_W: float(user_input[CONF_ESTIMATED_W]), CONF_CHANNELS: {}}
                return await self._after_details()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Required(CONF_ESTIMATED_PHASES, default=list(self._phases)): _phase_select(self._phases, True),
                vol.Required(CONF_ESTIMATED_W): _number(0, 100000, 50, "W"),
            }
        )
        return self.async_show_form(step_id="estimated", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if subentry.data.get(CONF_METER) or len(subentry.data.get(CONF_CHANNELS) or {}) == 1:
            return await self.async_step_reconfigure_meter(user_input)
        estimated = not subentry.data.get(CONF_CHANNELS)
        candidates = self._parent_candidates(exclude=subentry.subentry_id)
        parent_field = {vol.Optional(CONF_PARENT): self._parent_selector(candidates)} if candidates else {}
        errors: dict[str, str] = {}
        if estimated:
            schema = vol.Schema(
                {
                    vol.Required(CONF_NAME): selector.TextSelector(),
                    vol.Required(CONF_ESTIMATED_PHASES): _phase_select(self._phases, True),
                    vol.Required(CONF_ESTIMATED_W): _number(0, 100000, 50, "W"),
                    **parent_field,
                }
            )
            current = {CONF_NAME: subentry.data.get(CONF_NAME, subentry.title),
                       CONF_ESTIMATED_PHASES: subentry.data.get(CONF_ESTIMATED_PHASES, []),
                       CONF_ESTIMATED_W: subentry.data.get(CONF_ESTIMATED_W, 0)}
        else:
            schema = vol.Schema(
                {
                    vol.Required(CONF_NAME): selector.TextSelector(),
                    **{vol.Optional(p): METER_SELECTOR for p in self._phases},
                    **parent_field,
                }
            )
            current = {CONF_NAME: subentry.data.get(CONF_NAME, subentry.title), **subentry.data.get(CONF_CHANNELS, {}),
                       CONF_PORTABLE: bool(subentry.data.get(CONF_PORTABLE))}
            if subentry.data.get(CONF_INFO_ENTITY):
                current[CONF_INFO_ENTITY] = subentry.data[CONF_INFO_ENTITY]
            if len(subentry.data.get(CONF_CHANNELS, {})) == 1:
                schema = schema.extend({vol.Optional(CONF_PORTABLE): selector.BooleanSelector()})
            else:
                schema = schema.extend({vol.Optional(CONF_INFO_ENTITY): INFO_SELECTOR})
        current[CONF_PARENT] = subentry.data.get(CONF_PARENT) or NO_PARENT
        if user_input is not None:
            if estimated:
                if not user_input.get(CONF_ESTIMATED_PHASES):
                    errors[CONF_ESTIMATED_PHASES] = "no_phase"
                data = {CONF_NAME: user_input[CONF_NAME], CONF_CHANNELS: {},
                        CONF_ESTIMATED_PHASES: user_input.get(CONF_ESTIMATED_PHASES, []),
                        CONF_ESTIMATED_W: float(user_input[CONF_ESTIMATED_W])}
            else:
                channels = {p: user_input[p] for p in self._phases if user_input.get(p)}
                if not channels:
                    errors["base"] = "no_meter"
                for p, e in channels.items():
                    if not _unit_ok(self.hass, e):
                        errors[p] = "unsupported_unit"
                data = {CONF_NAME: user_input[CONF_NAME], CONF_CHANNELS: channels}
                if user_input.get(CONF_PORTABLE) and len(channels) == 1:
                    data[CONF_PORTABLE] = True
                if user_input.get(CONF_INFO_ENTITY) and len(channels) > 1:
                    data[CONF_INFO_ENTITY] = user_input[CONF_INFO_ENTITY]
            parent = user_input.get(CONF_PARENT)
            parent = None if parent in (None, NO_PARENT) else parent
            if parent and self._outside_parent(self._phases_of(data), parent):
                errors[CONF_PARENT] = "outside_parent"
            if parent:
                data[CONF_PARENT] = parent
            if not errors:
                return self.async_update_and_abort(self._get_entry(), subentry, title=data[CONF_NAME], data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
        )


    async def async_step_reconfigure_meter(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """A load with one meter: its phase, other phases it is wired to, and how power is split."""
        subentry = self._get_reconfigure_subentry()
        data_in = subentry.data
        candidates = self._parent_candidates(exclude=subentry.subentry_id)
        if data_in.get(CONF_METER):
            meter = data_in[CONF_METER]
            connected = list(data_in.get(CONF_CONNECTED) or [])
            draw = data_in.get(CONF_DRAW) or DRAW_EQUAL
            phase = draw if draw in connected else (connected[0] if connected else self._phases[0])
            current = {"phase": phase, CONF_CONNECTED: [p for p in connected if p != phase],
                       CONF_DRAW: DRAW_EQUAL if draw == DRAW_EQUAL else DRAW_PHASE}
        else:
            (phase, meter), = data_in[CONF_CHANNELS].items()
            current = {"phase": phase, CONF_CONNECTED: [], CONF_DRAW: DRAW_PHASE,
                       CONF_PORTABLE: bool(data_in.get(CONF_PORTABLE))}
        current.update({CONF_NAME: data_in.get(CONF_NAME, subentry.title), "entity": meter,
                        CONF_PARENT: data_in.get(CONF_PARENT) or NO_PARENT})
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Required("entity"): METER_SELECTOR,
                vol.Required("phase"): _phase_select(self._phases, multiple=False),
                **_multi_fields(self._phases, None),
                vol.Optional(CONF_PORTABLE): selector.BooleanSelector(),
                **({vol.Optional(CONF_PARENT): self._parent_selector(candidates)} if candidates else {}),
            }
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            if not _unit_ok(self.hass, user_input["entity"]):
                errors["entity"] = "unsupported_unit"
            parent = user_input.get(CONF_PARENT)
            parent = None if parent in (None, NO_PARENT) else parent
            if parent and self._outside_parent(_connected(user_input, self._phases), parent):
                errors[CONF_PARENT] = "outside_parent"
            if not errors:
                data = _one_meter_data(user_input[CONF_NAME], user_input["entity"], user_input, self._phases)
                if parent:
                    data[CONF_PARENT] = parent
                return self.async_update_and_abort(self._get_entry(), subentry, title=data[CONF_NAME], data=data)
        return self.async_show_form(
            step_id="reconfigure_meter",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
        )


def _multi_fields(phases: list[str], draw_default: str | None) -> dict:
    """'Also connected to' and 'Power is drawn' for a load with one meter."""
    draw_key = vol.Optional(CONF_DRAW, default=draw_default) if draw_default else vol.Optional(CONF_DRAW)
    return {
        vol.Optional(CONF_CONNECTED, default=[]): _phase_select(phases, multiple=True),
        draw_key: selector.SelectSelector(
            selector.SelectSelectorConfig(options=[DRAW_PHASE, DRAW_EQUAL], translation_key="draw",
                                          mode=selector.SelectSelectorMode.LIST)
        ),
    }


def _connected(user_input: dict[str, Any], phases: list[str]) -> list[str]:
    chosen = {user_input["phase"], *(user_input.get(CONF_CONNECTED) or [])}
    return [p for p in phases if p in chosen]


def _one_meter_data(name: str, meter: str, user_input: dict[str, Any], phases: list[str]) -> dict[str, Any]:
    """Single-phase (channels) or forced multi-phase with one meter (meter/connected/draw)."""
    connected = _connected(user_input, phases)
    if len(connected) == 1:
        data: dict[str, Any] = {CONF_NAME: name, CONF_CHANNELS: {user_input["phase"]: meter}}
        if user_input.get(CONF_PORTABLE):
            data[CONF_PORTABLE] = True
        return data
    draw = user_input["phase"] if user_input.get(CONF_DRAW, DRAW_EQUAL) == DRAW_PHASE else DRAW_EQUAL
    return {CONF_NAME: name, CONF_CHANNELS: {}, CONF_METER: meter, CONF_CONNECTED: connected, CONF_DRAW: draw}


class _SubLoad:
    """Adapter so the hierarchy helpers can walk subentries."""

    def __init__(self, sub: ConfigSubentry) -> None:
        self.subentry_id = sub.subentry_id
        self.parent = sub.data.get(CONF_PARENT)
        self.channels = sub.data.get(CONF_CHANNELS, {})


class DetectorSubentryFlow(ConfigSubentryFlow):
    """An unmetered-load detector, e.g. for a sauna without its own meter."""

    def _schema(self) -> vol.Schema:
        phases = self._get_entry().data[CONF_PHASES]
        return vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Required(CONF_DETECT_PHASES, default=list(phases)): _phase_select(phases, True),
                vol.Required(CONF_MIN_CURRENT_A, default=DEFAULT_MIN_CURRENT_A): _number(0.5, 100, 0.5, "A"),
                vol.Optional(CONF_HOLD_CURRENT_A): _number(0.5, 100, 0.5, "A"),
                vol.Required(CONF_ON_DELAY, default=DEFAULT_ON_DELAY): _number(0, 3600, 5, "s"),
                vol.Required(CONF_OFF_DELAY, default=DEFAULT_OFF_DELAY): _number(0, 7200, 30, "s"),
            }
        )

    def _validate(self, user_input: dict[str, Any]) -> dict[str, str]:
        if not user_input.get(CONF_DETECT_PHASES):
            return {CONF_DETECT_PHASES: "no_phase"}
        hold = user_input.get(CONF_HOLD_CURRENT_A)
        if hold is not None and hold > user_input.get(CONF_MIN_CURRENT_A, DEFAULT_MIN_CURRENT_A):
            return {CONF_HOLD_CURRENT_A: "hold_above_min"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate(user_input)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=self._schema(), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate(user_input)
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=user_input[CONF_NAME], data=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(self._schema(), user_input or dict(subentry.data)),
            errors=errors,
        )
