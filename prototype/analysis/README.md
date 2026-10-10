# Phase analysis prototypes

Throwaway scripts used on the reference installation to work out which main-fuse phase
each power meter is on. They read JSON dumps of Home Assistant recorder statistics (the
output of a `recorder/statistics_during_period` call, one file per time window) and
print a table. They are the starting point for the integration's auto-detection
(see `docs/design.md`, section 5).

| Script | Input | What it does |
|---|---|---|
| `phasemap.py` | 5-minute means, ~10 days | For each sub-meter, compares its 5-minute steps (≥ 0.8 A) with simultaneous changes in `sensor.current_l1..l3`; prints correlation and gain per phase |
| `phasemap90.py` | hourly means, ~90 days | Same with hourly steps; finds rarely used loads, misses short ones |
| `smallmap.py` | 5-minute means, ~10 days | Step-triggered, sign-aligned averaging for small loads (15–200 W); reports gain and a t-statistic per phase |
| `residual90.py` | hourly means, ~90 days | Main-fuse current minus every mapped sub-meter per phase; lists hours with unexplained load on all phases (sauna search) |

Run with the dump files as arguments, e.g.:

```sh
python3 phasemap.py window1.json window2.json window3.json
```

Entity ids for the main fuse (`sensor.current_l1..l3`) and the mapping in
`residual90.py` are specific to the reference installation.
