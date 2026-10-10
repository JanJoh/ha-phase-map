# Changelog

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
