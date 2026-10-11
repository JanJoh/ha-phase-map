# Changelog

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
