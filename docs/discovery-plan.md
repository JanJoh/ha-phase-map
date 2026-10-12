# Meter discovery: implementation plan (target 0.8.0)

*Status: draft, 2026-10-12. Open questions in section 11 are not decided yet.*

## 0. What was checked before designing

**HA 2026.10 cannot discover subentries.** This is the main constraint, and I checked it in the installed `homeassistant-2026.10.0` (`homeassistant/config_entries.py`):

- `ConfigSubentryFlowManager.async_create_flow` (around line 3816) does set `init_step = context["source"]`. But `ConfigSubentryFlow.async_create_entry` (around line 3904) refuses any source other than user:
  ```python
  if self.source != SOURCE_USER:
      raise ValueError(f"Source is {self.source}, expected {SOURCE_USER}")
  ```
  So a subentry flow started with `SOURCE_INTEGRATION_DISCOVERY` can never finish. The frontend also has no "Discovered" card for subentries.
- A config-flow discovery for the `phase_map` domain could add a subentry and then abort. But each "Ignore" would create a `SOURCE_IGNORE` config entry, one per sensor, in the integration list. **Rejected.**
- Repair issues fit. `async_create_issue(..., data=dict[str, str|int|float|None])` takes only flat data, so an issue carries just a candidate *key*, and the details live in a Store. `RepairsFlowManager.async_finish_flow` deletes the issue when the flow ends with `create_entry` and keeps it on abort. The "Ignore" button in the repairs UI sets `dismissed_version`, which survives restarts. `async_get_or_create` keeps `dismissed_version` when an issue is recreated, so re-raising an ignored issue does not bring it back.
- `hass.config_entries.async_add_subentry(entry, ConfigSubentry(...))` can be called directly. The code already does the equivalent in `engine.async_move_load` with `async_update_subentry`. The entry's update listener then reloads it.

**What the existing code gives us:**

- `detection.analyse()` is a pure function: it scores a meter series against phase series and returns `Suggestion(phase, confidence, steps, scores)`. `async_fetch_means()` already batches several ids into one `statistics_during_period` call in the recorder executor.
- `energy_prefs.suggest_parent()` works on any list of meter entities.
- `config_flow` already holds the right subentry shapes: `_one_meter_data`, the `channels` dict, and `parent`.
- Every subentry change reloads the entry. **So the scanner must not rescan on every setup.**

---

## 1. Candidate selection (new `discovery.py`, pure functions plus registry reads)

### 1a. Which entities qualify
Sensors from `er.async_get(hass).entities` together with `hass.states` qualify when all of these hold:

- `domain == "sensor"`
- `device_class ∈ {power, current}` (from state attributes, or the registry's `original_device_class` / `device_class`)
- `state_class == "measurement"` (needed for statistics; it also rules out energy (kWh) sensors, which are `total` / `total_increasing`)
- unit is W, kW, A or mA (reuse `SUPPORTED_UNITS` and `detection.unit_factor`)
- not disabled; `entity_category` is None; not hidden by the integration

### 1b. Exclusions
Each excluded entity is recorded with a reason; diagnostics show them.

| Reason | Rule |
|---|---|
| `main_fuse` | the entity is a main-fuse source, **or it is on the same device as one** (a P1/HAN reader's total, export and per-phase power; a load balancer) |
| `used` | used as a channel or meter by any load in **any** `phase_map` entry |
| `own` | registry `platform == "phase_map"` |
| `taken_group` | in the same *channel group* (see 1c) as a used entity. This covers the other channels of an already-added Shelly Pro 3EM, and power/current twins of an added channel |
| `aggregate` | a device "total"/"sum" entity when that device also has phase-tagged channels |
| `platform` | the platform is on a deny list: `group`, `min_max`, `utility_meter`, `integration`, `derivative`, `statistics`, `filter`. `powercalc` is on it by default (it estimates rather than measures; open question) |
| `twin` | in the same channel group as a better choice (see 1d) |
| `duplicate_of:<entity>` | after analysis, the 5-minute series is almost identical to an added load or another candidate (correlation > 0.98, ratio about 1, or about V for W against A). This catches one meter integrated twice (Shelly native plus MQTT) |
| `idle` / `too_new` / `never_switches` | from the evidence stage (section 2) |
| `ignored` | in the Store's ignored list |

### 1c. Channel groups per device
For each `device_id`, collect the qualifying entities and work out a **channel key**:

1. Strip the device's slug prefix from the `object_id`.
2. Strip measurement words: `power, active_power, effekt, current, strom, ström, watt, load`.
3. What is left is the key: `phase_a`, `l1`, `channel_1`, `switch_0`, or empty for the whole device.

**Phase-tagged keys** match `^(phase_?|fas_?)?(l?[123]|[abc])$`. All phase-tagged keys of one device form one **multi-phase group candidate** (Shelly Pro 3EM a/b/c; the garage's `garaget_l1/l2/l3_effekt`). Any other key is its **own single candidate** (Emporia circuits, a Pro 4PM's switches, a single plug).

> **Decision:** the channel names in the tag ("phase_a", "L3") are **only used for grouping, never as the phase.** The phase always comes from the evidence. In the user's home the Shelly's channel c is on L2, and the garage's "L3" channel is on house L1.

Entities without a device become single candidates keyed by the registry entry id.

### 1d. Twins (power and current of the same channel)
Keep **one** entity per channel key, in this order:

1. the entity the Energy dashboard references (`stat_rate`);
2. the one that has statistics;
3. current (A) over power (W), as the README recommends;
4. the lower entity_id.

The other twins become `twin`.

### 1e. Stable candidate key
`dev:<device_id>:<group|channel_key>`, or `ent:<registry_entry.id>`. Registry ids survive renames, so a renamed entity does not come back after being ignored.

---

## 2. Evidence

### 2a. Statistics: three stages per scan, cheapest first

1. **Prefilter** with hourly `mean`/`max`/`min` over 10 days, in batches of 50 ids:
   - `too_new` if there is less than 2 days of data;
   - `idle` if the max is under 0.1 A (about 23 W) or the max−min spread is under 0.1 A.

   Candidates that pass are ranked by spread, with Energy-dashboard devices first.
2. **Full analysis** with 5-minute `mean` over `min(10, purge_keep_days)` days, in **batches of 10 ids**. These run in the recorder executor through the existing `async_fetch_means`.
   - The **main fuse** and **every metered load's entities** are fetched once per scan and reused.
   - `analyse()` runs in `hass.async_add_executor_job` per batch, with `await asyncio.sleep(1)` between batches.
3. **Reference choice per candidate:**
   - Run `analyse` against the main fuse.
   - Also run it against each metered load's `channels` (possible parents). A **parent** counts when that run gives `confidence == "high"` with gain between 0.7 and 1.5. If several parents qualify, pick the deepest one (via `hierarchy.descendants`).
   - The suggestion then uses the parent's channels, as `_finish` in the load flow already does. The resulting phase is a house phase, because parent channels are keyed by main-fuse phase.

### 2b. Changes in `detection.py`
- `analyse(..., confounders: list[dict[float, float]] | None = None)`: skip step pairs where any confounder moved by more than `CONFOUND_RATIO`·|step|. The default `None` keeps today's behaviour exactly.
- New `analyse_group(channels: dict[key, series], phases) -> GroupSuggestion`:
  1. For each channel, analyse only its **isolated steps** (pass the sibling channels as confounders).
  2. Choose the channel→phase **permutation** with the highest sum of t.
  3. Every assigned channel must be at least `medium`, and the best sum must be at least 1.5× the runner-up permutation.

  This resolves the Shelly case (a→L1, c→L2, b→L3) as long as single-phase sub-loads step on their own. A **balanced 3-phase load** with only simultaneous steps returns `reason="balanced"` and no mapping.
- If two channels of a group map to the same phase, `analyse_group` returns `reason="not_a_phase_group"`. The group is then split into single candidates.
- New `similar(a, b) -> bool` for the duplicate check.

### 2c. Minimum evidence before anything is offered
- **Single candidate:** confidence at or above the option `discovery_min_confidence` (default **high**: t ≥ 6, gain 0.7–1.5, ≥ 10 steps). `medium` is allowed when the user lowers the setting.
- **Group:** a valid permutation, with every channel at or above the setting.
- `balanced` groups, `unclear` results and `aggregate` results (gain ≥ 0.3 on more than one phase) are **never raised as repairs**. They appear in the options review list as "could not place", so the user can still add them by hand.

### 2d. Live phase-check machinery (phase 2, not the MVP)
- **Do not** feed candidates into `engine.checker`. Its `response()` treats every recorded series as a possible confounder. An unconfigured garage sub-meter would then spoil the garage's own check events.
- Instead, add a separate `PhaseChecker` instance in a `CandidateWatcher`. It tracks the main fuse, the configured loads and **at most 20 shortlisted candidates** (medium or unclear, but with enough spread).
- It reuses `LoadCheck` / `classify` / `response`, with `configured=None` and the reference set to the parent channels or the main fuse.
- Agreement of `MOVE_VOTES` out of `EVIDENCE` events, with statistics not contradicting it, promotes the candidate to "offerable" (`source: "live"`).

### 2e. Cost and throttling (constants in `const.py`)
- `SCAN_INTERVAL = 24 h`. The first scan runs `STARTUP_DELAY = 10 min` after `EVENT_HOMEASSISTANT_STARTED`.
- **On setup or reload, never rescan.** Load the Store and reconcile the issues. Schedule the next scan from `last_scan + SCAN_INTERVAL`.
- After a configuration change (a reload where the set of load subentries changed), run a **quick pass** after 2 min. It re-analyses only candidates that have evidence (capped at 10), because parents and references may have changed.
- `MAX_ANALYSED_PER_SCAN = 100`, oldest `analysed_at` first. Unclear or idle candidates back off 1, 2, 4 then 7 days. After 4 unclear analyses spread over at least 30 days, the status becomes `never_switches` and it is checked again only when the hourly spread changes noticeably.
- Hundreds of sensors, say 400: 8 prefilter queries of hourly rows (cheap), then at most 100 full analyses in 10 batches of about 2,880 rows per id. That is spread over a few minutes in a background task (`hass.async_create_background_task`) and cancelled on unload.
- `entity_registry_updated` with `action == "create"`: only record `first_seen` in the Store (debounced save). There is no immediate analysis, because there is no history yet.

---

## 3. How it is surfaced

> **Key decision: the primary mechanism is fixable repair issues, one per candidate, with at most `MAX_OPEN_SUGGESTIONS = 5` open at a time.** The options flow gets a "Suggested meters" review step that lists everything and manages the ignored list.

**Why repairs:**
- They are the only native place in 2026.10 that combines a visible badge, a flow that can create things, and a persistent per-item Ignore.
- The pattern is already used and tested here (`load_moved` in `engine.py` and `repairs.py`).
- The cap keeps the Repairs page from flooding on the first scan. Each new issue appears as earlier ones are resolved, straight from the cache with no rescan.

Rejected or deferred alternatives:
- **Config-flow discovery:** ignored config entries pile up (see section 0).
- **Persistent notification:** it cannot act.
- **Sensor:** phase 2, a diagnostic-category count with an attribute list, useful for automations.
- **Websocket and card panel:** a read-only command in the MVP, with the UI in phase 2 in the card repo.
- **Options flow only:** too hard to find, so it is secondary.

**The issue:**
- id `meter_<entry_id>_<hash(candidate_key)>`
- `translation_key="new_meter"` or `"new_multi_meter"`
- placeholders: name, phase or mapping, confidence, steps, gain, parent
- `data={"entry_id", "key"}`
- severity `WARNING`; `is_persistent=False`, because the manager recreates it from the Store

**Fix flow (`NewMeterFlow` in `repairs.py`; `async_create_fix_flow` dispatches on the issue_id prefix):**

1. A `confirm` form, prefilled:
   - **name:** device `name_by_user` or `name`, else the friendly name without its measurement suffix
   - **single candidate:** `phase` (choices limited to the parent's phases when behind a parent), `parent` (with the existing `NO_PARENT` option and labels), `portable`
   - **group:** a per-phase `EntitySelector` prefilled with the mapping, the same field style as `async_step_multi`
2. Submit:
   - Validate again: still unused, still supported unit, and the parent's phases cover the load's phases (`outside_parent`).
   - `async_add_subentry(entry, ConfigSubentry(data=MappingProxyType(data), subentry_type="load", title=name, unique_id=None))`
   - `create_entry`, which deletes the issue; the entry then reloads.
3. A second menu option, `ignore` ("Not a load / don't suggest again"): add the key to the Store's ignored list, then `create_entry`.
4. Aborts: `already_added` and `candidate_gone`.

**Ignore and re-offer rules:**
- The **Store is the source of truth**. Store `phase_map.discovery.<entry_id>`:
  ```
  {last_scan, candidates: {key: {entities, device_id, name, status, suggestion, group_mapping,
                                 parent, parent_source, analysed_at, unclear_runs, first_seen}},
   ignored: {key: {name, entities, at, reason}}, known_used: [entity_ids]}
  ```
- **Native Ignore:** on reconcile, an issue with `dismissed_version` set is mirrored into `ignored` with reason `repairs_ignore`. The issue is left in place so its dismissed state is kept.
- **Removed loads:** entities that were in `known_used` and are no longer used (the user deleted the load) are automatically ignored with reason `removed_load`. **Open question.**
- **Not shown again** unless the user un-ignores them in the options flow.
- **An offered candidate is not withdrawn** because its evidence weakens (no flip-flopping). It is withdrawn when its entity disappears, becomes used, or is ignored, or when discovery is turned off.
- **If the suggested phase changes between scans**, the placeholders are updated.

---

## 4. Settings (options flow)

> **Decision:** `async_step_init` becomes a **menu** with `settings`, `suggestions` and `ignored`. The current form moves unchanged to `async_step_settings`. This breaks the options-flow calls in `tests/test_config_flow.py` (line ~64) and `tests/test_labels.py` (lines ~97 and ~133). They need one extra `{"next_step_id": "settings"}` step.

- **`settings`** adds a section `discovery`, collapsed when discovery is off:
  - `discovery` (bool, default **on**; open question)
  - `discovery_min_confidence` (select `high`/`medium`, translation_key `discovery_confidence`)

  Turning discovery off deletes all `meter_*` issues and stops the manager. The Store is kept, including the ignored list.
- **`suggestions`:**
  - A read-only description listing each candidate with its status (offered / could not place / too new / idle) and evidence.
  - A multi-select "Ignore these".
  - A "Scan now" boolean that sets `force_scan` in the Store, so the scan runs 30 s after the reload.
- **`ignored`:** a multi-select of the ignored items, all checked. Unchecking one un-ignores it.
- **Saving:** these steps write the Store, then `async_create_entry(data=dict(self.config_entry.options))`. That reload is harmless.

---

## 5. Hierarchy, multi-phase candidates and phase labels

### Parent precedence
1. `energy_prefs.suggest_parent()` with the candidate's entities. The user has declared this, so it wins (`parent_source="energy"`).
2. Otherwise the statistical parent test from 2a (`parent_source="stats"`).
3. If the Energy-dashboard parent and the statistics strongly disagree, keep the Energy parent but add a sentence to the description: "History suggests it is not behind X".

### Energy points at a parent that is not added yet
When `included_in_stat` points at another **candidate**, order the parent first. The child's description says "add Garage first". Its issue is not raised until the parent is added; the quick pass then re-analyses the child against the garage channels.

### The user's garage
- The garage group `garaget_l1/l2/l3_effekt` is analysed against the main fuse and its mapping comes out as `{L1: garaget_l3, …}`, in house phases.
- Once the garage is added, its sub-meters are compared with the garage's channels (much less noise) and get house phases with parent = garage.
- `effective_parents` rules still apply: the parent must measure every phase the load uses.

### Other multi-phase cases
- A group creates a `channels` load, the same as "multi".
- One-meter multi-phase loads (a CEE outlet as a total meter) cannot be inferred. They are offered as single-phase, and the user can change that later through Reconfigure. Detecting them is phase 2+.
- **Reparenting** (a new candidate that is the parent of existing root loads) is phase 2: "Garage meter found; 3 of your loads seem to be behind it."

### Phase labels
- Added loads get labels automatically, because `async_sync_labels` runs at setup.
- **Hint:** if a candidate's device carries exactly one label matching `label_names(entry)`, and that label is **not** in this integration's `labels` Store `added` list (so the user set it), show "Device label says L2" in the confirm form. A disagreement with the evidence is shown, never resolved silently.
- Labels the integration added itself (for example on another Emporia circuit of the same device) are never used as hints.

---

## 6. Diagnostics, translations, docs

**`diagnostics.py`** gets a `discovery` key with:
- `enabled`, `min_confidence`, `last_scan`, `next_scan`, `counts_by_status`
- `candidates`: key, entities, device_id, status, `suggestion.as_dict()`, group mapping and scores, parent and `parent_source`, label hint, `analysed_at`, `unclear_runs`
- `excluded`: `{reason: [entity_ids]}`, capped at 50 per reason plus a count
- `ignored`
- `open_issues`

**`strings.json` and `translations/en.json` + `sv.json`:**
- `issues.new_meter` and `issues.new_multi_meter`: title, `fix_flow.step.confirm` (data and data_description for name/phase/parent/portable and the per-phase fields), `fix_flow.step.init` menu (`add`/`ignore`), `fix_flow.abort` (`already_added`, `candidate_gone`), `fix_flow.error.outside_parent`
- `options.step.init.menu_options`, `options.step.settings` (the moved form plus `sections.discovery`), `options.step.suggestions`, `options.step.ignored`
- `selector.discovery_confidence`
- Swedish wording to match the existing tone: "Ny mätare hittad: {name}", "Föreslå nya mätare", "Ignorerade mätare". Placeholder text for parents stays in Python, following the existing `NO_PARENT_TEXT` pattern.

**README:**
- Replace the first bullet of "What HA Phase Map works out by itself" with: **"New meters: it looks once a day for power and current sensors you have not added, works out their phase (and the meter they sit behind), and offers the clearest ones under Settings → Repairs. It never adds anything by itself."**
- New section "Suggested meters": what qualifies, what is skipped, how evidence is gathered, the 5-at-a-time rule, Ignore, settings and the ignored list, and that balanced 3-phase loads and never-switching meters must be added by hand.
- Add `phase_map/discovery` to the websocket table.
- Update the Configure sentence in Setup.

**`docs/design.md`:** rewrite section 6 to match this design (it currently says "discovery flow"). Update the status line and the architecture box.

**`CHANGELOG.md`:** add a 0.8.0 entry. **`manifest.json`:** set `"version": "0.8.0"`.

---

## 7. File-level changes

| File | Change |
|---|---|
| `custom_components/phase_map/discovery.py` (new) | `Candidate`, `channel_key`, `group_entities`, `select_candidates(hass, entry) -> (candidates, excluded)`, `async_evaluate(...)`, `DiscoveryManager` (Store, schedule, quick pass, issue reconcile, registry listener, start/stop) |
| `detection.py` | `analyse(confounders=)`, `analyse_group`, `GroupSuggestion`, `similar`; `async_fetch_means` takes `types` (needed for the hourly min/max prefilter) |
| `const.py` | `CONF_DISCOVERY`, `CONF_DISCOVERY_CONFIDENCE`, defaults, scan constants, `ISSUE_NEW_METER = "meter_"`, platform deny list |
| `__init__.py` | create `DiscoveryManager(hass, entry, engine)` after `engine.async_start()` and keep it as `engine.discovery`; `async_on_unload(manager.async_stop)`; new `async_remove_entry` that deletes the discovery Store and `meter_*` issues |
| `repairs.py` | `NewMeterFlow`; dispatch on the issue_id prefix |
| `config_flow.py` | options menu with `settings` / `suggestions` / `ignored`; move the shared helpers (`_one_meter_data`, the parent selector and label building, `_parent_phases`, `_outside_parent`) to a small `subentries.py` so the repair flow builds **identical** subentry data |
| `websocket.py` | `phase_map/discovery {entry_id}` (read-only candidate list) in the MVP; `discovery/ignore` and `discovery/scan` in phase 2 |
| `diagnostics.py` | `discovery` section |
| `strings.json`, `translations/en.json`, `translations/sv.json` | as listed in section 6 |
| `README.md`, `docs/design.md`, `CHANGELOG.md`, `manifest.json` | as listed in section 6 |
| `tests/…` | section 8 |

---

## 8. Test plan

Run with `docker run --rm -v $PWD:/work -w /work phase-map-test python -m pytest`.

**`tests/test_discovery_select.py`.** Pure and registry tests. Create registry entries and devices with `er.async_get_or_create(..., device_id=, original_device_class=)` and states via a `set_amps` variant that also sets `state_class`.
1. A qualifying W sensor is a candidate; a kWh `total_increasing` sensor, a `voltage` sensor and a VA sensor are not.
2. Main-fuse sources **and their device's siblings** are excluded as `main_fuse`.
3. Entities used by loads in this or **another** `phase_map` entry are excluded as `used`.
4. `platform == "phase_map"`, `entity_category` diagnostic, disabled entities and deny-list platforms are excluded.
5. A Shelly Pro 3EM with `phase_a/b/c_current`, `phase_a/b/c_active_power` and `total_active_power` gives **one** group of 3 current entities, with the total excluded as `aggregate` and the powers as `twin`.
6. The same device with one channel already used by the heat-pump load: the whole group is `taken_group`.
7. An Emporia-like device with `circuit_1..4`, one of them used: the 3 others remain separate candidates.
8. The twin preference picks the `stat_rate` entity from the Energy dashboard over the A entity.
9. A garage with `garaget_garaget_l1/l2/l3_effekt` is grouped by the `l1/l2/l3` tags.
10. An ignored key stays out, and still does after an entity_id rename (key = registry id).

**`tests/test_discovery_analysis.py`.** Synthetic series, no HA.
11. `analyse` with `confounders=None` gives the same result as before on an existing fixture (regression).
12. `analyse_group` maps the Shelly case **a→L1, c→L2, b→L3** from isolated steps.
13. The garage case: channel "l3" maps to L1, and the result is not influenced by the name.
14. A balanced 3-phase load with only simultaneous steps gives `reason="balanced"` and no mapping.
15. Two channels on the same phase give `not_a_phase_group`.
16. `similar()` flags a W/A twin (ratio about 230) and a doubly integrated plug.
17. A solar-like series (negative correlation) is never clear; a house-total template is classified `aggregate`.

**`tests/test_discovery_manager.py`.** Patch `custom_components.phase_map.discovery.async_fetch_means` and use the `hass_storage` and `freezer` fixtures.
18. The first scan runs `STARTUP_DELAY` after start and creates issues for `high` candidates only, at most **5**; the sixth appears after one is resolved, **without** another fetch.
19. Batching: each fetch call has ≤ 10 ids (5-minute) or ≤ 50 (hourly); the main fuse is fetched once per scan; ≤ `MAX_ANALYSED_PER_SCAN`.
20. Reloading the entry does **not** rescan (fetch is not called) when `last_scan` is recent; issues are restored from the Store.
21. A configuration change triggers the quick pass, limited to candidates with evidence.
22. Backoff: an unclear candidate is re-analysed after 1, 2 and 4 days, and becomes `never_switches` after 4 runs over 30 days.
23. `too_new` and `idle` from the hourly prefilter never reach the 5-minute fetch.
24. Energy-dashboard parent: patch `async_energy_devices`; the issue placeholder shows the garage and the issue data carries the key.
25. Statistical parent: the candidate steps with garage channel L1, so parent = garage and phase = L1; the deepest of nested parents is chosen.
26. Energy says the parent is an *unadded* candidate: the child's issue is withheld until the parent exists.
27. The device label hint is used only for a user-set label, not one this integration added (via the labels Store).
28. Turning discovery off removes all `meter_*` issues and keeps the ignored list.
29. Removing a load auto-ignores its entity (`removed_load`).
30. An issue ignored in the Repairs UI (`ir.async_ignore_issue`) is mirrored to the Store's ignored list and is not deleted.
31. A candidate whose entity disappears, or that is added by hand, has its issue deleted on reconcile.

**`tests/test_discovery_repairs.py`.** Use `repairs_flow_manager` as `test_phase_check.py` does.
32. Add a single meter: the subentry data is exactly `{"name", "channels": {"L2": e}}`, plus `parent` when one is chosen; the issue is deleted and the entry reloads.
33. Add a group: `channels` follows the mapping, and editing a channel in the form is respected.
34. A phase outside the parent's phases gives the `outside_parent` error.
35. The entity was added by hand meanwhile: abort `already_added`, and the issue is kept.
36. Ignore from the fix-flow menu: Store updated, issue deleted, not raised again after a scan.

**`tests/test_config_flow.py` / `tests/test_labels.py` (updated) plus `tests/test_discovery_options.py`.**
37. The options menu leads to `settings`; the existing margin and label tests pass through `next_step_id: settings`.
38. The discovery toggle and min confidence are saved; `medium` then lets a medium candidate raise an issue.
39. The `ignored` step: unchecking un-ignores, and the item is raised again from the cache.
40. "Scan now" schedules a scan 30 s after the reload.

**Others:**
41. `test_support.py::test_diagnostics`: the `discovery` section exists and the result passes `json.dumps`.
42. `test_websocket.py`: `phase_map/discovery` returns the candidates; unknown entry gives `not_found`.
43. `test_recorder.py` with `recorder_mock`: the real `statistics_during_period` with `types={"mean","min","max"}` and `period="hour"` returns empty series; `select_candidates` plus the prefilter on an empty database gives `too_new` without raising.
44. A translation test: every `issues.new_meter*` and options key exists in en and sv (simple JSON key comparison).

---

## 9. Phased delivery

**0.8.0 (MVP):**
- Candidate selection, grouping and exclusions; twin and duplicate checks
- Statistics-only evidence (single and group with isolated steps)
- Energy-dashboard and statistical parent hints
- Repair issues per candidate, capped, with add and ignore
- Store and re-offer rules; options menu (settings / suggestions / ignored / scan now)
- Read-only `phase_map/discovery` websocket command
- Diagnostics, en and sv translations, README, design.md, CHANGELOG
- Tests 1–44

**0.8.x / 0.9.0:**
- `CandidateWatcher` for live evidence on shortlisted candidates
- Card panel "Suggested meters", with add and ignore over websocket, in the ha-phase-map-card repo and synced with `scripts/sync_card.sh`
- Diagnostic count sensor
- Reparenting suggestions (a new branch meter above existing roots)
- One-meter multi-phase hints
- Guided test mode (design.md 5a) for balanced 3-phase and never-switching meters

---

## 10. Risks
- **False positives:** template sums, double integrations, solar or battery sensors. Mitigated by the deny list, `aggregate`, `similar()`, a default of `high`, and repairs only ever *suggesting*.
- **Recorder load on SQLite or a Raspberry Pi.** Mitigated by the hourly prefilter, batches of 10, a cap of 100 per scan, one scan a day, and no rescan on reload.
- **Using repairs for something that is not a problem.** This is acceptable for a custom integration, and the cap of 5 limits the noise.
- **Balanced 3-phase loads (heat pumps)** may never be resolvable from 5-minute statistics. They are listed but not offered; live evidence and the guided test come later.
- **Several `phase_map` entries** (two main fuses) could offer the same sensor twice. Exclusion of "used anywhere" covers added sensors. For unadded ones, offer them only in the entry where the evidence is clear, with the lowest entry_id breaking ties.
- **The options flow becomes a menu**, which is a small UX change and needs test updates.

## 11. Decisions (2026-10-12)

1. **Off by default**, also on upgrade. **Energy dashboard as a seed:** the devices listed under
   *Individual devices* (`device_consumption` with a `stat_rate`) are meters the user has already
   picked out. Offer them first, with a lower bar (medium confidence), and use `included_in_stat`
   for the parent. A one-time "Suggest meters from the Energy dashboard" action is available in
   Configure even with discovery off, and as an optional step at the end of the main-fuse setup.
2. **High confidence, at most 5 open at a time, one repair per meter** (granular), as planned.
3. **powercalc and template power sensors are excluded** for now.
4. **Power or current:** pick the unit that matches the main fuse's sensors (current when the
   fuse is measured in A, power when in W), because then the gain is about 1 with no
   power-factor or voltage error. If only one is available, use it. The Energy dashboard's
   `stat_rate` decides only between equal choices. *(Confirmed.)*
5. **Deleting a load ignores its sensor** from then on (reason `removed_load`), for now.
6. **Balanced 3-phase groups:** offer them with a guessed mapping, marked as a guess. The card
   draws the guessed phases as unverified until live evidence confirms them.
7. **Sensors hidden by the user are not considered**, for now.
