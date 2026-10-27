# Changelog

## 0.10.0 (2026-10-27)

- **The card's boxes are clickable**: a load opens its meter, *Unmetered* the phase's
  unmetered-current sensor, and a detector its power sensor.
- The websocket payload carries the entity ids for this: `entity_id` per load (its meter, or
  the first phase's channel), `unmetered_entity_id` per phase and `entity_id` per detector.
- Bundles phase-bus-card **0.9.0**.

## 0.9.4 (2026-10-13)

- **A load on one phase no longer keeps a detector on after the session.** An air fryer
  without a meter on L2 plus a few seconds of meter glitch on L1 every few minutes counted as
  "two phases still running" and restarted the off-delay each time, so the sauna detector
  stayed on long after the sauna was off. The keep-on condition now has to last a minute
  before it restarts the off-delay; a stage that really runs lasts much longer than that.
- **The power sensor ignores such loads:** it counts only while a stage of the detected load
  runs (all phases but one above the keep-on level), so the air fryer is not reported as sauna.

## 0.9.3 (2026-10-13)

- **Detectors keep their state across a restart or reload.** Before, a detector always started
  "off" and needed every phase to rise again; a sauna heater at part power (two phases) never
  does, so automations saw the sauna as off until the thermostat brought back full power.

## 0.9.2 (2026-10-13)

- **Renamed to "Phase Map"** in Home Assistant (integrations list, devices, dialogs) and in HACS.
  The domain (`phase_map`), entity ids and the repository stay the same, so nothing needs
  setting up again.
- Bundles phase-bus-card **0.8.1** (the same rename in its texts).

## 0.9.1 (2026-10-13)

- **Detectors follow heaters that switch off a stage at a time.** A sauna heater dropped one
  phase when it got close to its set temperature and kept heating on the other two; the
  detector needed all three phases and switched off while the sauna was still on. Now a
  detector turns on when every phase rises by the minimum, and stays on while all phases but
  one are above a lower *keep on* level (new, optional; default two thirds of the minimum).
  A glitch on a single phase still neither keeps it on nor starts it.
- **A one-phase glitch no longer restarts the on-delay.** Meters that update out of step can
  make one phase dip for a few seconds; while the detector waits out its on-delay, the keep-on
  level applies too.
- **The power sensor counts the stage that is left**: each phase's current above normal (from
  1 A) while the detector is on, instead of 0 W unless every phase was above the minimum.

## 0.9.0 (2026-10-13)

- **Power and energy for detected loads.** Every detector's device now has a power sensor
  (what the load draws now, estimated from unmetered current above normal) and an energy sensor
  (kWh, for the Energy dashboard). The power is 0 W while the detector is off, and while it is
  on but the load pauses, so a sauna's thermostat cycles show up.
- Bundles phase-bus-card **0.8.0**: detectors are drawn as loads on the phases they watch (a
  dashed box marked *detected*, with the reading per phase at the junctions), and what they see
  is taken out of *Unmetered*. In average mode they stay chips under the diagram.
- The websocket payload's `detectors` carry `a`, `w` (per phase), `power_w` and `heating`.

## 0.8.1 (2026-10-13)

- **Detectors survive a restart mid-session.** The quiet level of unmetered current that a
  detector compares against now starts from the last 6 hours of recorded history when the
  integration starts or reloads. Before, it started from zero and relearned from whatever was
  running, so a restart while the sauna was on taught it the sauna as "normal" and switched the
  detector off about 25 minutes later.
- **Nothing is learned while a detector is on,** so a long session can't become the new normal
  either.

## 0.8.0 (2026-10-12)

- **Suggested meters** (off by default; Configure → Settings → Suggest new meters). Once a day
  HA Phase Map looks for power and current sensors no load uses, works out their phase from
  history (and the meter they sit behind, from the Energy dashboard or from history), and
  offers the clearest ones as repairs, at most 5 at a time: add it as a load from a prefilled
  form, or never suggest it again. Multi-channel meters (Shelly Pro 3EM) are one suggestion
  with their channels matched to phases from the evidence, not the channel names; when the
  evidence is not clear the mapping is offered as a guess.
- **Suggest meters from the Energy dashboard** (Configure): offers the Energy dashboard's
  individual devices that are not added yet, also while discovery is off.
- **Configure is a menu:** Settings, Suggest meters from the Energy dashboard, Suggested meters
  (what was found, ignore, scan now) and Ignored meters (bring them back).
- A sensor whose load is deleted is not suggested again.
- Diagnostics include what discovery found and skipped; new websocket command
  `phase_map/discovery`.

## 0.7.2 (2026-10-12)

- Bundles phase-bus-card **0.7.0**: multi-phase loads show each phase's reading at the junction
  in the phase colour, and the per-phase split in the box is coloured by phase.

## 0.7.1 (2026-10-12)

- **Reload note.** When the main fuse is added, the dialog now says to add loads next and to
  reload the browser before looking for *Phase Bus Card* in the card picker (the card is loaded
  when the page loads). The README's installation steps say the same and now describe HACS.

## 0.7.0 (2026-10-12)

- **The card is included.** The integration serves the phase-bus card and loads it in the
  frontend itself: one install, no dashboard resource to add. The URL carries a hash of the
  file, so browsers pick up a new card after an update. A separately installed copy of the card
  does no harm; the first one loaded is used.
- **Diagnostics download** (⋮ → Download diagnostics): configuration, how loads nest, the live
  values, phase-check evidence, verified phases and every meter's state, for bug reports.
- **At most one update per second.** Meters that report every second no longer trigger a
  recompute, a state write per sensor and a push to every open card each time; the first change
  is handled at once and the latest one at the end of the second.
- Issue templates for bug reports and suggestions.

## 0.6.0 (unreleased)

- **Verified phases.** For every multi-phase load, the payload lists the phases it has been
  seen drawing from (`verified`). With a meter per phase: the phase's meter showed at least
  0.1 A, live or in the last 30 days of statistics. With one meter for several phases: the
  main fuse (or the parent meter) moved on that phase when the load switched, in 2 live events
  or clearly in the last 10 days of statistics. A 3-phase charger that has only ever charged a
  1-phase car is verified on that one phase.

## 0.5.0 (unreleased)

- **Multi-phase loads with one meter.** A load measured by a single total meter (a CEE outlet,
  a charger measured as a total) can be declared multi-phase whatever its history shows: pick
  its phase and **Also connected to** the other phases it is wired to. It is drawn as a
  multi-phase load and is not checked for moves.
- **Power is counted** decides how that meter's power is split: all on the phase picked (a
  1-phase car on a 3-phase charger), or equally over the connected phases. Editing the load can
  change both, and turn it back into a single-phase load.
- Editing a load with one meter now shows its meter, phase and these options instead of one
  field per phase. Such a load can also be a parent; meters behind it are then checked against
  the main fuse, limited to its phases.

## 0.4.0 (unreleased)

- **Phase labels on devices** (options → *Phase labels on devices*, off by default): every
  load's meter device gets a label such as "Fas: 2"; a device that measures several phases
  gets one per phase. The label name (default "Fas") and the value per phase (default 1, 2, 3;
  for example R, S, T) are free text. Labels follow when a load moves or the settings change,
  and labels HA Phase Map created are deleted once nothing uses them.
- Only labels HA Phase Map added are ever removed; labels that were already on a device stay.
  Turning the option off asks whether to remove the labels or keep them.
- README: known issues link.

## 0.3.0 (unreleased)

- **Phase check.** Every single-phase load's phase is checked continuously from live
  switch-on and switch-off events (steps of 0.5 A or more): the reference phase that moved
  by about the same current, while the others stayed flat, is where the load is. Events where
  another meter moved at the same time are skipped. Loads behind a parent are checked against
  the parent's channels.
- When 5 of the last 6 events point at another phase, a **Repair** asks to move the load
  ("Kettle seems to have moved to L3"); confirming updates it. Evidence that points back at
  the configured phase clears the repair.
- **Portable loads**: a new option for single-meter loads that are moved between sockets.
  They follow the evidence without asking, and the move is written to the logbook.
- The websocket payload has a `phase_check` per single-phase load (configured phase, events
  agreeing and disagreeing, suspected phase, last event).

## 0.2.0 (unreleased)

- **Meters behind other meters.** A load can be placed behind another meter, for example a
  CEE outlet, a heater and a water heater behind the meter on a garage's incoming cable.
  Only meters without a parent are subtracted from the main fuse, so nothing is counted
  twice, and each parent reports what is left of it per phase (`rest_w` in the websocket
  payload and averages).
- Adding a load asks which meter it sits behind, pre-selected from the Energy dashboard's
  "upstream device" when it has one. Behind a parent, the phase suggestion compares the
  meter with the parent's channels instead of the main fuse, and only offers the parent's
  phases. Editing a load can change its parent; a load cannot be placed behind itself or
  one of its own children.
- Detector devices link to the fuse device with `via_device_id` (the `via_device` form is
  deprecated and stops working in Home Assistant 2027.8).

## 0.1.0 (unreleased)

First version.

- Main fuse as the config entry: 1, 2 or 3 phases, voltage, fuse rating, a sensor per phase
  (A, mA, W or kW); reconfigure and options for the overload warning.
- Loads as subentries: one meter on one phase (with a phase suggested from the last 10 days
  of statistics), multi-phase loads with one meter per phase, and estimated loads without a
  meter.
- Sensors per phase: unmetered current and headroom. Binary sensors per phase: overload risk.
- Unmetered load detectors as subentries, with on/off delays and a learned normal level.
- Websocket API for the card: entries, live subscription, averages, phase suggestion.
- English and Swedish translations.
