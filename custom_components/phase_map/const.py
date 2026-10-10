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

# Subentry types
SUBENTRY_LOAD = "load"
SUBENTRY_DETECTOR = "detector"

# Load subentry
CONF_NAME = "name"
CONF_CHANNELS = "channels"        # {phase: entity_id}
CONF_ESTIMATED_W = "estimated_w"  # estimated power for unmetered loads (W, total)
CONF_ESTIMATED_PHASES = "estimated_phases"

# Detector subentry
CONF_DETECT_PHASES = "detect_phases"
CONF_MIN_CURRENT_A = "min_current_a"
CONF_ON_DELAY = "on_delay_s"
CONF_OFF_DELAY = "off_delay_s"

DEFAULT_VOLTAGE = 230
DEFAULT_FUSE_A = 20
DEFAULT_MARGIN_A = 1.0
DEFAULT_OVERLOAD_DELAY = 60
DEFAULT_MIN_CURRENT_A = 6.0
DEFAULT_ON_DELAY = 60
DEFAULT_OFF_DELAY = 900

PHASE_NAMES = {1: ["L1"], 2: ["L1", "L2"], 3: ["L1", "L2", "L3"]}

# How the detector learns the normal unmetered current per phase
BASELINE_SAMPLE_S = 60            # one sample per minute
BASELINE_WINDOW = 6 * 60          # keep 6 hours of samples
BASELINE_QUANTILE = 0.2           # 20th percentile = typical quiet level

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"   # .format(entry_id)
