# Changelog

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
