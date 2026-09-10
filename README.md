# Višnjan Sky Ledger

An all-sky photometric survey run from [Višnjan Observatory](https://en.wikipedia.org/wiki/Vi%C5%A1njan_Observatory)
(45.291°N, 13.749°E), imaging every tile visible from the site in two broadband
filters, and keeping a version-controlled record of what has actually been observed.

**[Open the planner →](https://USER.github.io/visnjan-sky-ledger/)**

## The survey

| | |
|---|---|
| Field of view | 2.45° × 1.63° |
| Tile overlap | 10% on each edge |
| Tiles | 8,631 covering Dec > −20° |
| Area | 27,919 deg² |
| Exposure | 30 s, filters G and R |
| Per clear night | ~230 tiles (~740 deg²) at zero overhead |
| One full G+R pass | ~30 clear nights |

At a 40° altitude floor only Dec ≥ −4.7° is ever reachable from this latitude,
so ~1,700 tiles of the nominal footprint need a lower floor to be observable at
all. The planner shows the reachable limit for whatever floor you set.

## How a night works

```
plan  →  observe  →  reconcile  →  commit
```

**1. Plan.** Either in the browser (open the page, pick a date, Generate plan,
Download plan JSON) or headlessly:

```bash
python tools/plan_night.py 2026-09-12 --min-alt 40 --moon-keepout 30
```

Both use the same algorithm: skip tiles already complete in both filters, drop
anything below the altitude floor or inside the moon keep-out, seed on the
best-placed remaining tile, then grow outward so the night lands as one compact
block. Tiles are ordered serpentine by declination row to keep slews short.

**2. Build the sequence.** Point it at your own working NINA sequence as the
template — its Start and End areas are copied through byte-for-byte, and only
the `Targets` container is replaced:

```bash
python tools/build_sequence.py data/nights/night_2026-09-12.json \
       --template templates/observatory_base.json
```

Each block is `Switch Filter → Run Autofocus → 30 panels`, alternating G and R
over the same tiles, so the filter wheel moves once per block and every tile
finishes in both colours before the night moves on. Panels carry **no
conditions** — in NINA a container with conditions repeats while they hold true,
so a per-panel condition makes each panel loop forever instead of advancing.

**3. Reconcile.** Record what was actually taken, not what was planned:

```bash
python tools/reconcile.py 2026-09-12 --fits /path/to/night/*.fits
```

Each frame is matched to its tile by coordinates. Tiles seen in both G and R
become complete.

**4. Commit.** `data/coverage.json` is the ledger. Committing it is what makes
the survey's history reproducible and the coverage map update.

## Weather

```bash
python tools/fetch_weather.py 5      # refresh data/weather.json from met.no
```

The page shows a go/no-go per night from the stored forecast: clear if peak
cloud ≤ 25% and median humidity < 90%, marginal to 60%, otherwise no-go. Set
`WEATHER_UA` to your own contact string — met.no requires a descriptive
User-Agent.

## Layout

```
index.html            planner + coverage map (static; GitHub Pages)
data/coverage.json    the ledger — observed tiles per night
data/weather.json     forecast snapshots
data/nights/          one plan per night
tools/grid.py         tile grid; the JS in index.html mirrors it exactly
tools/plan_night.py   headless planner
tools/build_sequence.py   plan -> NINA Advanced Sequencer file
tools/reconcile.py    frames -> coverage
tools/fetch_weather.py    met.no -> weather.json
templates/            your NINA sequence, used as the Start/End donor
sequences/            generated NINA files
```

`tools/grid.py` and the JavaScript grid builder implement the same rings-of-
constant-declination algorithm, so tile ids mean the same thing in both.

## Requirements

`numpy` and `astropy` for the Python tools; `astropy` also for reading FITS
headers in `reconcile.py`. The page itself has no build step and no dependencies —
it computes twilight, sidereal time and lunar position in the browser.

## Notes

Filter wheel positions in `tools/build_sequence.py` (`G=2, R=1`) were inferred
from an existing sequence rather than read from the NINA profile. Verify them
against Options → Equipment → Filter Wheel before a run.

## License

MIT
