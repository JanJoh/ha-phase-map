# Phase Map: design

Status: draft, 2026-10-10. Written from a working prototype on one installation
(3 × 230 V, 3 × 20 A main fuse, ~20 sub-meters).

## 1. Problem

Homes with a small main fuse have several big loads (heat pump, EV charger, oven,
sauna, immersion heater) spread over the phases in ways nobody remembers. Owners want to:

1. **Know which load is on which phase**, and how much headroom each phase has.
2. **See loads that have no meter**, because main-fuse current minus all sub-meters
   leaves an "unmetered rest" that is normally small.
3. **Act on that**, for example block a heat pump while an unmetered sauna runs, so the
   main fuse does not blow.
4. **See it as a picture**: main fuse, one bus per phase, loads branching off.

Existing tools cover parts: the Energy dashboard knows consumption but not phases;
power-flow and Sankey cards draw flows but have no notion of a phase or a fuse limit;
EV chargers balance only themselves.

## 2. Architecture

Two deliverables, distributed through HACS:

```
┌──────────────────────── Home Assistant ────────────────────────┐
│                                                                 │
│  phase_map integration (Python)          phase-bus-card (TS/Lit)│
│  ├─ config entry: main fuse              ├─ reads layout + values│
│  ├─ subentries: one per load             │  over websocket       │
│  ├─ sensors: unmetered / headroom        ├─ draws the bus diagram│
│  ├─ binary sensors: load detectors       └─ live or N-day average│
│  ├─ phase auto-detection (recorder)                              │
│  ├─ new-meter discovery                                          │
│  └─ websocket API  ◄──────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

Everything runs inside Home Assistant. No cloud, no external token.

## 3. Data model

### Config entry: the main fuse

| Field | Example | Notes |
|---|---|---|
| `phases` | `3` | 1, 2 (split-phase) or 3 |
| `voltage` | `230` | nominal phase voltage, for A ↔ W conversion |
| `fuse_a` | `20` | rated current per phase |
| `source[L1..Ln]` | `sensor.current_l1` | current (A) or power (W) per phase at the main fuse |
| `source_unit` | `A` | `A` or `W`; W sources are divided by voltage |
| `phase_labels` | `L1, L2, L3` | display names |

### Subentry: one per load

| Field | Example | Notes |
|---|---|---|
| `name` | `Oven` | display name; `\|` allowed for a two-line label |
| `kind` | `single` / `multi` | multi = 3-phase (or 2-phase) load |
| `channels` | `[{phase: L3, entity: sensor.effektmatare_spis_ugn_effekt}]` | one per phase it draws from; a 3-phase meter gives one channel per phase |
| `included_in` | subentry id | nested meter; subtracted only once (can be imported from the Energy dashboard's `included_in_stat`) |
| `assumed` | `false` | true for unmetered loads drawn from an estimate (e.g. sauna 8 kW) |
| `assumed_w` | `8000` | estimate used for scenarios and the card when `assumed` |
| `controllable_by` | `garo` / none | informational: the load already limits itself |
| `detection` | see 5 | confidence, step count, gain, date; null when assigned by hand |

Why subentries and not labels: they are editable in the UI with validation, carry
structured fields (channels, nesting, estimates) and belong to the integration. Labels
remain an optional mirror for users who filter by them.

## 4. Entities the integration creates

Per phase:

- `sensor.phase_map_<ph>_unmetered_current`: main-fuse current minus every channel on
  that phase (A, measurement, long-term statistics)
- `sensor.phase_map_<ph>_headroom`: `fuse_a` minus main-fuse current (A)
- `binary_sensor.phase_map_<ph>_overload_risk`: headroom below a margin for a time

Per detector (configured as its own subentry type):

- `binary_sensor.phase_map_<name>`: an **unmetered load detector**. It turns on when
  the unmetered current on its phases rises by at least `min_a` within `rise_s`, stays
  on while it holds (with `off_delay_s` to ride out thermostat cycling), and requires
  all listed phases together for multi-phase loads. A sauna is one configuration:
  `phases: [L1, L2, L3], min_a: 6, off_delay_s: 900`.

Blocking the heat pump stays in a normal automation that uses the detector. The
integration does not switch devices itself.

## 5. Phase auto-detection

Prototype: `prototype/analysis/phasemap*.py`, `smallmap.py`.

For a meter channel `X` and main-fuse phases `L1..Ln`, over recorder statistics
(5-minute means for the last ~10 days; hourly means for longer windows):

1. Take steps: `dX = X[t] − X[t−1]` (in A), keep steps with `|dX| ≥ max(0.8 A, 25 % of
   the largest step)` for big loads, or `≥ 15 W` with sign-aligned averaging for small ones.
2. For each phase, regress `dL` on `dX` over those steps: **gain** (≈ 1.0 when right,
   ≈ 0 elsewhere) and a **t-statistic** for the mean sign-aligned response.
3. Suggest the phase with the highest t if `t ≥ 3`, `gain ≥ 0.6` and t is at least twice
   the runner-up. Otherwise report "unclear: needs more on/off events or a test run".

Observed on the reference house: big loads (dishwasher, oven, compressor) give gains of
0.9–1.2 within 3 days; fridges need 10 days of 5-minute data; constant loads (UPS) can
never be placed this way. Hourly statistics over 90 days find rarely used loads but miss
short ones.

A **guided test** mode covers the rest: "switch X on now". The integration watches the
main fuse at full resolution for 2 minutes and reports the phase with the matching jump.

## 6. New-meter discovery

- Listen to `entity_registry_updated` (`create`) and to state for sensors with
  `device_class: power` or `current` that no subentry uses and that are not ignored.
- Skip aggregates automatically when possible: a device's "total" next to per-phase
  channels, and PoE or UPS-internal sensors; let the user ignore the rest.
- Offer a **discovery flow** ("New power meter found: X"), pre-filled with the
  auto-detected phase once enough data exists, or ask for a guided test.

## 7. Card data contract

Websocket command `phase_map/layout` (subscription, pushes on change):

```json
{
  "fuse": {"phases": ["L1","L2","L3"], "fuse_a": 20, "voltage": 230},
  "window": {"mode": "average", "days": 10},
  "phases": {
    "L1": {"total_w": 791, "unmetered_w": 384},
    "L2": {"total_w": 403, "unmetered_w": 193},
    "L3": {"total_w": 569, "unmetered_w": 14}
  },
  "loads": [
    {"id": "…", "name": "Oven", "phases": ["L3"], "w": {"L3": 21}, "assumed": false},
    {"id": "…", "name": "EV charger (Garo)", "phases": ["L1","L2","L3"],
     "w": {"L1": 137, "L2": 0, "L3": 0}, "assumed": false}
  ]
}
```

`mode: live` uses current states. `mode: average` uses recorder statistics over `days`,
computed in the integration (`recorder/statistics_during_period`), not in the browser.

## 8. Card rendering rules (from the prototype)

- Main fuse box on the left; one horizontal bus per phase in the phase colour.
- Single-phase loads hang below their bus as boxes, sorted by power, with an
  "Unmetered rest" box (dashed) at the end of each row.
- Multi-phase loads sit to the right: one conductor per phase in that phase's colour,
  a dot where it joins its own bus, and a small arc where it crosses a bus it does not
  connect to. Assumed loads are dashed.
- Names and power only. Colours from HA theme variables (with sensible defaults for
  L1/L2/L3), light/dark aware; horizontal scroll on narrow screens; text alternative for
  screen readers.

## 9. Generality

| Case | Handling |
|---|---|
| Single-phase homes | `phases: 1`; one bus, no multi-phase loads |
| Split-phase (US 2 × 120 V) | `phases: 2`; 240 V loads are 2-phase loads |
| Sources in W, A or VA | per-source unit; VA treated as W with a note |
| Solar export, negative power | clamp loads at 0; the main fuse uses absolute current |
| Multi-channel meters (Shelly Pro 3EM, CTs) | one channel entity per phase |
| Nested meters | `included_in`, imported from Energy dashboard prefs |

## 10. Packaging and quality

- HACS: `hacs.json`, `custom_components/phase_map/`, card served by the integration
  (registered as a frontend resource) or as a separate HACS plugin.
- CI: `hassfest`, HACS validation, `pytest-homeassistant-custom-component`, card build
  and lint.
- Translations: English and Swedish.
- Development against a separate Home Assistant in Docker, never the live installation.

## 11. Open questions

1. **Distribution:** HACS expects one integration per repository with
   `custom_components/<domain>/` at the top. The planned public home, `JanJoh/ha-addons`,
   is a Supervisor add-on repository (`repository.yaml`). Options: a separate public
   repository for the integration (recommended), or a monorepo with the card bundled in
   the integration.
2. **Card shipping:** bundled with the integration (one install) or a separate HACS plugin?
3. **License:** MIT, as is customary for HA custom components?
4. **First version scope:** manual assignment + sensors + card first, auto-detection and
   discovery in version 2?
5. **Name:** `phase_map` / "Phase Map", or something else?
