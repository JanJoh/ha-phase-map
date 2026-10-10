# Phase Map for Home Assistant

See which loads sit on which phase of your main fuse, how much headroom each phase has,
and which big loads are drawing power that no meter accounts for.

Phase Map is planned as a Home Assistant custom integration plus a dashboard card:

- **Integration (`phase_map`)**: you pick the sensors that measure your main fuse and
  assign every power meter to a phase (or let it suggest the phase from history). It
  creates sensors for unmetered current and headroom per phase, and an "unmetered load"
  detector, for example to notice a sauna without its own meter and block a heat pump
  while it runs.
- **Card (`phase-bus-card`)**: a single-line diagram with the main fuse, one bus per
  phase, and each load branching off its phase, with live or average power.

> **Status:** design and prototype. Nothing here installs into Home Assistant yet.
> This repository is private while the design settles; it will later move to a public
> repository.

## What is here now

| Path | Contents |
|---|---|
| [`docs/design.md`](docs/design.md) | Architecture, data model, setup flow, card data contract, phase auto-detection, open questions |
| [`prototype/phase-bus/`](prototype/phase-bus/) | Standalone generator for the bus diagram: `loads.example.json` → `phase-bus.html` |
| [`prototype/analysis/`](prototype/analysis/) | The scripts that worked out which phase each meter is on, from Home Assistant statistics |

### Try the diagram prototype

```sh
python3 prototype/phase-bus/phase_bus.py            # uses loads.json, else loads.example.json
open prototype/phase-bus/phase-bus.html
```

The example data is a real three-phase, 3 × 20 A Swedish house with about twenty
metered loads, an EV charger, a CEE outlet and an unmetered sauna.

## Requirements (planned)

- Home Assistant 2026.8 or newer (config subentries)
- A sensor for current or power per phase at the main fuse (P1/HAN port, Shelly Pro 3EM,
  Tibber Pulse, Garo Entity Balance or similar)
- Recorder long-term statistics for the meters you want to place automatically

## License

To be decided before the repository goes public.
