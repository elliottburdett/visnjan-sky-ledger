# Sky Ledger

A two-site photometric survey — [Višnjan Observatory](https://en.wikipedia.org/wiki/Vi%C5%A1njan_Observatory)
in Croatia and [GoChile](https://gochile.si/) at El Sauce in Chile — imaging the
sky in two broadband filters and keeping a version-controlled record of what has
actually been observed.

Between the two sites the whole celestial sphere is reachable: Višnjan at +45.3°
sees down to Dec −4.7° at a 40° altitude floor, GoChile at −30.5° sees up to
Dec +19.5°. Neither site alone can do it.

**[Open the planner →](https://elliottburdett.github.io/visnjan-sky-ledger/)**

## Telescopes

| | Višnjan | GoChile GoT1 | GoChile GoT2 |
|---|---|---|---|
| Site | Tičan, Croatia | El Sauce, Chile | El Sauce, Chile |
| Latitude | +45.291° | −30.473° | −30.473° |
| Optics | wide-field imager | 400 mm f/6.5 RC, 2600 mm | 72 mm f/5.6 refractor, 403 mm |
| Camera | — | IMX455 mono, 36.0×24.0 mm | IMX571 colour, 23.5×15.7 mm |
| Field of view | 2.45° × 1.63° | 0.793° × 0.529° | 3.338° × 2.231° |
| Pixel scale | — | 0.30″/px | 1.92″/px |
| Filters | Antlia G, R | SLOAN/SDSS g′, r′ | L + duo-band Ha/OIII |
| Tile (10% overlap) | 3.23 deg² | 0.34 deg² | 6.03 deg² |
| Tiles to cover its sky | 8,631 | 81,686 | 4,557 |
| Reachable Dec at 40° alt | −4.7° … +90° | −80.5° … +19.5° | −80.5° … +19.5° |

GoChile fields of view are derived from aperture × f-ratio and the sensors named
on [gochile.si](https://gochile.si/teleskop-in-oprema/); neither the focal length
nor the FOV is published directly, and the figures assume no focal reducer.
**Measure a plate solve against them before trusting the tiling.**

### Choosing between the GoChile instruments

They pull in opposite directions and neither is an obvious survey engine:

- **GoT1** carries the Sloan g′/r′ photometric filters, so it is the only
  GoChile instrument that can do this survey's two-band photometry. But its tile
  is a tenth of Višnjan's, so covering its accessible sky takes **~220 clear
  nights** — a multi-year commitment even at El Sauce's ~300 clear nights a year.
  It suits a targeted southern footprint or deep fields far better than an
  all-sky pass.
- **GoT2** is four times faster than Višnjan per night and would sweep its whole
  sky in about 13 nights — but it is a one-shot colour camera with only L and
  duo-band Ha/OIII. No g′/r′, so no two-band photometry.

Both are defined in `tools/grid.py`; switching is a one-line change. The
planner defaults to Višnjan.

## The survey

| | |
|---|---|
| Exposure | 30 s per filter, both sites |
| Tile overlap | 10% on each edge |
| Per clear night | ~230 tiles at Višnjan, ~366 at El Sauce (longer nights) |
| Coverage record | `data/coverage.json`, keyed per telescope |

At a 40° altitude floor each site can only ever reach part of the sky, and the
planner shows the reachable limits for whatever floor you set.

## How a night works

```
plan  →  observe  →  reconcile  →  commit
```

**1. Plan.** Either in the browser (open the page, pick a date, Generate plan,
Download plan JSON) or headlessly:

```bash
python tools/plan_night.py 2026-09-12 --telescope visnjan
python tools/plan_night.py 2026-09-12 --telescope gochile-got1 --end 03:00
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
tools/grid.py         telescope table + tile grids; the JS in index.html mirrors it
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

The NINA sequence builder targets the Višnjan rig. GoChile is operated through
its own scheduling system, so plans for it export as CSV rather than a NINA
sequence.

## License

MIT
