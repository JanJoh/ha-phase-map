"""Constants for HA Phase Map."""

from __future__ import annotations

DOMAIN = "phase_map"

# Config entry (the main fuse)
CONF_PHASES = "phases"            # list of phase names, e.g. ["L1", "L2", "L3"]
CONF_PHASE_COUNT = "phase_count"  # 1, 2 or 3 (setup flow only)
CONF_VOLTAGE = "voltage"          # nominal phase voltage (V)
CONF_FUSE_A = "fuse_a"            # rated current per phase (A)
CONF_SOURCES = "sources"          # {phase: entity_id} measuring the main fuse
CONF_MARGIN_A = "margin_a"        # overload warning margin below the fuse (A)
CONF_OVERLOAD_DELAY = "overload_delay_s"
CONF_LABELS = "labels"            # label load devices with their phase
CONF_LABEL_NAME = "label_name"    # e.g. "Fas"; the label reads "<name>: <value>"
DEFAULT_LABEL_NAME = "Fas"


def phase_value_key(phase: str) -> str:
    """Option holding the label value for a phase, e.g. "1" or "R"."""
    return f"phase_value_{phase}"

# Subentry types
SUBENTRY_LOAD = "load"
SUBENTRY_DETECTOR = "detector"

# Load subentry
CONF_NAME = "name"
CONF_CHANNELS = "channels"        # {phase: entity_id}
CONF_ESTIMATED_W = "estimated_w"  # estimated power for unmetered loads (W, total)
CONF_ESTIMATED_PHASES = "estimated_phases"
CONF_PARENT = "parent"            # subentry id of the meter this load sits behind, if any
NO_PARENT = "none"
CONF_PORTABLE = "portable"        # follow the load to a new phase without asking
# One meter for a load connected to several phases (a CEE outlet with a single total meter)
CONF_METER = "meter"              # the meter's entity_id
CONF_CONNECTED = "connected_phases"   # the phases it is wired to (how the card draws it)
CONF_DRAW = "draw"                # how the meter's power is split: DRAW_EQUAL or a phase name
DRAW_EQUAL = "equal"

# Detector subentry
CONF_DETECT_PHASES = "detect_phases"
CONF_MIN_CURRENT_A = "min_current_a"   # every phase this far above normal: the load starts
CONF_HOLD_CURRENT_A = "hold_current_a" # all phases but one this far above normal: it still runs (a stage of it)
CONF_ON_DELAY = "on_delay_s"
CONF_OFF_DELAY = "off_delay_s"

DEFAULT_VOLTAGE = 230
DEFAULT_FUSE_A = 20
DEFAULT_MARGIN_A = 1.0
DEFAULT_OVERLOAD_DELAY = 60
DEFAULT_MIN_CURRENT_A = 6.0
HOLD_SHARE = 2 / 3                # hold level when none is set: two thirds of the start level
DETECTOR_NOISE_A = 1.0            # while on, a phase counts towards the power from this much above normal
DEFAULT_ON_DELAY = 60
DEFAULT_OFF_DELAY = 900

PHASE_NAMES = {1: ["L1"], 2: ["L1", "L2"], 3: ["L1", "L2", "L3"]}

# How the detector learns the normal unmetered current per phase
BASELINE_SAMPLE_S = 60            # one sample per minute
BASELINE_WINDOW = 6 * 60          # keep 6 hours of samples
BASELINE_QUANTILE = 0.2           # 20th percentile = typical quiet level

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"   # .format(entry_id)

# Meter discovery: suggest power/current sensors that are not added yet (off by default)
CONF_DISCOVERY = "discovery"
CONF_DISCOVERY_CONFIDENCE = "discovery_min_confidence"   # "high" or "medium"
DEFAULT_DISCOVERY = False
DEFAULT_DISCOVERY_CONFIDENCE = "high"
ISSUE_NEW_METER = "meter_"        # repair issue id prefix: meter_<entry_id>_<hash>
