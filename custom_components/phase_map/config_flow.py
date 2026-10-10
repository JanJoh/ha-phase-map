"""Config flow: the main fuse as the config entry, loads and detectors as subentries."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_CHANNELS,
    CONF_DETECT_PHASES,
    CONF_ESTIMATED_PHASES,
    CONF_ESTIMATED_W,
    CONF_FUSE_A,
    CONF_MARGIN_A,
    CONF_MIN_CURRENT_A,
    CONF_NAME,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_OVERLOAD_DELAY,
    CONF_PHASE_COUNT,
    CONF_PHASES,
    CONF_SOURCES,
    CONF_VOLTAGE,
    DEFAULT_FUSE_A,
    DEFAULT_MARGIN_A,
    DEFAULT_MIN_CURRENT_A,
    DEFAULT_OFF_DELAY,
    DEFAULT_ON_DELAY,
    DEFAULT_OVERLOAD_DELAY,
    DEFAULT_VOLTAGE,
    DOMAIN,
    PHASE_NAMES,
    SUBENTRY_DETECTOR,
    SUBENTRY_LOAD,
)
from .detection import async_suggest_phase

SUPPORTED_UNITS = {"A", "mA", "W", "kW"}
CONFIDENCE_TEXT = {"en": {"high": "high", "medium": "medium"}, "sv": {"high": "hög", "medium": "medel"}}

METER_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=["current", "power"])
)


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
    """Warning margin and delay for the overload binary sensors."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_MARGIN_A, default=opts.get(CONF_MARGIN_A, DEFAULT_MARGIN_A)): _number(0, 50, 0.5, "A"),
                vol.Required(CONF_OVERLOAD_DELAY, default=opts.get(CONF_OVERLOAD_DELAY, DEFAULT_OVERLOAD_DELAY)):
                    _number(0, 3600, 5, "s"),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class LoadSubentryFlow(ConfigSubentryFlow):
    """Add or edit a load: one meter, a multi-phase meter, or an estimated unmetered load."""

    def __init__(self) -> None:
        self._name = ""
        self._meter = ""
        self._suggestion = None

    @property
    def _phases(self) -> list[str]:
        return self._get_entry().data[CONF_PHASES]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["meter", "multi", "estimated"])

    # One meter on one phase, with a suggested phase from history
    async def async_step_meter(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not _unit_ok(self.hass, user_input["entity"]):
                errors["entity"] = "unsupported_unit"
            else:
                self._name = user_input[CONF_NAME]
                self._meter = user_input["entity"]
                entry = self._get_entry()
                self._suggestion = await async_suggest_phase(
                    self.hass, self._meter, entry.data[CONF_SOURCES], float(entry.data[CONF_VOLTAGE])
                )
                return await self.async_step_phase()
        schema = vol.Schema({vol.Required(CONF_NAME): selector.TextSelector(), vol.Required("entity"): METER_SELECTOR})
        return self.async_show_form(step_id="meter", data_schema=schema, errors=errors)

    async def async_step_phase(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Pick the phase; pre-selected from history when the analysis was clear."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._name, data={CONF_NAME: self._name, CONF_CHANNELS: {user_input["phase"]: self._meter}}
            )
        sug = self._suggestion
        found = bool(sug and sug.phase)
        default = sug.phase if found else self._phases[0]
        schema = vol.Schema({vol.Required("phase", default=default): _phase_select(self._phases, multiple=False)})
        if found:
            score = sug.scores[sug.phase]
            lang = "sv" if (self.hass.config.language or "").startswith("sv") else "en"
            placeholders = {
                "name": self._name,
                "phase": sug.phase,
                "confidence": CONFIDENCE_TEXT[lang][sug.confidence],
                "steps": str(sug.steps),
                "gain": f"{score.gain:.2f}" if score.gain is not None else "–",
            }
            return self.async_show_form(step_id="phase", data_schema=schema, description_placeholders=placeholders)
        return self.async_show_form(
            step_id="phase_manual",
            data_schema=schema,
            description_placeholders={"name": self._name, "steps": str(sug.steps if sug else 0)},
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
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data={CONF_NAME: user_input[CONF_NAME], CONF_CHANNELS: channels}
                )
        schema = vol.Schema(
            {vol.Required(CONF_NAME): selector.TextSelector(), **{vol.Optional(p): METER_SELECTOR for p in self._phases}}
        )
        return self.async_show_form(step_id="multi", data_schema=schema, errors=errors)

    # A load without a meter, drawn from an estimate (e.g. a sauna)
    async def async_step_estimated(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_ESTIMATED_PHASES):
                errors[CONF_ESTIMATED_PHASES] = "no_phase"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_NAME: user_input[CONF_NAME],
                          CONF_ESTIMATED_PHASES: user_input[CONF_ESTIMATED_PHASES],
                          CONF_ESTIMATED_W: float(user_input[CONF_ESTIMATED_W]), CONF_CHANNELS: {}},
                )
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
        estimated = not subentry.data.get(CONF_CHANNELS)
        errors: dict[str, str] = {}
        if estimated:
            schema = vol.Schema(
                {
                    vol.Required(CONF_NAME): selector.TextSelector(),
                    vol.Required(CONF_ESTIMATED_PHASES): _phase_select(self._phases, True),
                    vol.Required(CONF_ESTIMATED_W): _number(0, 100000, 50, "W"),
                }
            )
            current = {CONF_NAME: subentry.data.get(CONF_NAME, subentry.title),
                       CONF_ESTIMATED_PHASES: subentry.data.get(CONF_ESTIMATED_PHASES, []),
                       CONF_ESTIMATED_W: subentry.data.get(CONF_ESTIMATED_W, 0)}
        else:
            schema = vol.Schema(
                {vol.Required(CONF_NAME): selector.TextSelector(), **{vol.Optional(p): METER_SELECTOR for p in self._phases}}
            )
            current = {CONF_NAME: subentry.data.get(CONF_NAME, subentry.title), **subentry.data.get(CONF_CHANNELS, {})}
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
            if not errors:
                return self.async_update_and_abort(self._get_entry(), subentry, title=data[CONF_NAME], data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
        )


class DetectorSubentryFlow(ConfigSubentryFlow):
    """An unmetered-load detector, e.g. for a sauna without its own meter."""

    def _schema(self) -> vol.Schema:
        phases = self._get_entry().data[CONF_PHASES]
        return vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Required(CONF_DETECT_PHASES, default=list(phases)): _phase_select(phases, True),
                vol.Required(CONF_MIN_CURRENT_A, default=DEFAULT_MIN_CURRENT_A): _number(0.5, 100, 0.5, "A"),
                vol.Required(CONF_ON_DELAY, default=DEFAULT_ON_DELAY): _number(0, 3600, 5, "s"),
                vol.Required(CONF_OFF_DELAY, default=DEFAULT_OFF_DELAY): _number(0, 7200, 30, "s"),
            }
        )

    def _validate(self, user_input: dict[str, Any]) -> dict[str, str]:
        return {CONF_DETECT_PHASES: "no_phase"} if not user_input.get(CONF_DETECT_PHASES) else {}

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
