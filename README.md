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

## Passes, not percent done

The sky is never "finished". A **pass** is one complete visit to a tile in every
survey filter, and tiles can be revisited indefinitely — repeat epochs are the
point of a variable-star and transient survey, not wasted time. Coverage is
therefore tracked as *depth*:

```
passes(tile) = min over filters of (times that filter was taken)
```

A tile with G but no R yet sits at zero passes and shows as *partial*; the
planner will finish it before moving on. `--pass 2` targets everything that has
not yet reached two complete visits, which usually means re-walking sky you have
already covered once. The map shades 0 / partial / 1 / 2 / 3+ passes, and the
header reports coverage *at the pass you are currently building*.

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

**2. Build the sequence.** Either press **Download NINA .json** in the web
planner, or run the headless builder against your own working sequence, whose
Start and End areas are copied through byte-for-byte:

```bash
python tools/build_sequence.py data/nights/night_2026-09-12_visnjan.json \
       --template templates/observatory_base.json
```

Both produce the same structure. The night's tile list is cut into blocks of
30 tiles and each block is imaged once per filter:

```
Targets
  Wait Until Safe
  Switch G → Run Autofocus → 30 panels           (block order)
  Switch R → Run Autofocus → the same 30 panels  (reversed)
  Switch G → Run Autofocus → next 30 panels
  ...
```

and a panel is a single `DeepSkyObjectContainer` holding one exposure:

```
Target preparation   Stop Guiding → Slew to Ra/Dec (abort) → Center (abort) → Start Guiding
Target imaging       expose
```

The filter wheel therefore moves twice per block rather than twice per panel,
and focus is re-measured on every filter change — which is where focus actually
moves, since G and R do not come to focus in the same place. The temperature
and HFR triggers on the Targets container still run on top of that, catching
drift inside a block. Several details are deliberate and worth leaving alone:

- **Every panel carries three conditions**, and the third one is not optional:

  | Condition | Iterations / value | What it does |
  |---|---|---|
  | Loop | 1 | lets the panel run exactly once |
  | Safety Monitor | — | skips the panel unless the sky is safe |
  | Time | the night's cutoff | skips the panel unless it can finish in time |

  Conditions are ANDed, and NINA re-evaluates them before *every* instruction,
  walking up the parent chain — so clouds arriving between the slew and the
  exposure stop the panel before the shutter opens, and the cutoff cannot catch
  a frame half-taken. The Loop condition is what makes this safe: in NINA a
  container with conditions *repeats* while they hold true, so Safety Monitor
  and Time on their own would pin the mount on one tile and re-image it forever
  instead of advancing. Do not remove it. (A container with **no** conditions
  falls back to `Iterations < 1`, which is why the earlier files worked.)

  When a check fails, the same conditions on the Targets container fail too, so
  the sequence leaves the target list and runs the End area — find home, close
  cover, warm camera. Note that an unsafe reading and a *disconnected* safety
  monitor are the same thing to NINA: `IsSafe = Connected && IsSafe`.
- **Every Switch Filter carries its own inline `FilterInfo`** rather than a
  shared `$ref`. Sharing one filter object across hundreds of instructions is
  what made earlier files misbehave.
- **Slew, Center and Switch Filter abort on error**; only the exposure continues.
  A silently failed slew or filter change produces a night of confidently
  mislabelled data, which is worse than a stopped sequence.
- The explicit **Slew** runs before Center rather than relying on Center to move
  the mount.
- **Guiding is stopped and restarted explicitly around the slew.** Slew and
  Center each stop the guider and restart it themselves if it was running, so
  leaving it up across a panel boundary costs *two* settles per panel. Stopping
  it before the slew and starting it after the solve gives exactly one, on the
  final pointing. Both guider instructions continue on error — a field with no
  usable guide star should cost one unguided sub, not the night.
- **Settle time is part of the plan, not a surprise.** The planner budgets
  `2 × (exposure + settle)` per tile, so the **Settle (s)** box has to match
  what PHD2 actually takes or the night will overrun. Set **Guide each panel**
  to *No* to drop the instructions and the settle budget together; the headless
  builder reads the same choice from the plan's `params.guide`, or takes
  `--no-guide`.
- **Every second filter block retraces its tiles in reverse**, so the mount
  starts each block from where it finished the last one instead of driving back
  across the whole region.
- **Panel names carry the coordinates** — `RA221235_Dec+300000_G` — so a frame
  can be matched back to its tile and filter from the header alone, with no
  side-car file.
- **`Targets` opens with Wait Until Safe**, so a sequence started early parks
  itself at the top of the block list rather than at an arbitrary panel.

**3. Reconcile.** Record what was actually taken, not what was planned:

```bash
python tools/reconcile.py 2026-09-12 --fits /path/to/night/*.fits
```

Each frame is matched to its tile by coordinates. Tiles seen in both G and R
become complete.

**4. Commit.** `data/coverage.json` is the ledger. Committing it is what makes
the survey's history reproducible and the coverage map update.

## Simulated nights

To exercise the planner without waiting for clear sky, generate synthetic
observations from a plan:

```bash
python tools/plan_night.py 2026-09-20 --pass 1
python tools/mock_observe.py data/nights/night_2026-09-20_visnjan.json \
       --completion 0.6 --seeing 2.5 --fail-rate 0.03 --seed 1
```

It writes frame metadata to `data/mock/frames_<date>_<telescope>.csv` in the same
format `reconcile.py` consumes, then folds it into `coverage.json` through the
same coordinate-matching path real frames take. Plan the next night and it will
route around what the mock "observed"; ask for `--pass 2` and it will go back
over it.

`--completion` is the honest knob: 0.6 reflects roughly what per-panel overhead
actually leaves you, so the simulated ledger fills at a believable rate.

**It is fake, and marked as fake in four places:** `"mock": true` on the night
record, a note beginning `SYNTHETIC`, a `mock=1` column on every frame row, and a
magenta banner across the web page whenever any mock night is loaded — with a
checkbox to exclude it from coverage entirely. It simulates frame *metadata*
only; no pixels, nothing that could be mistaken for imagery. To purge it, delete
`data/mock/` and drop the `"mock": true` entries from `coverage.json`.

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
data/coverage.json    the ledger — observed tiles per night, per telescope
data/mock/            synthetic frame lists (delete to purge simulated data)
data/weather.json     forecast snapshots
data/nights/          one plan per night
tools/grid.py         telescope table + tile grids; the JS in index.html mirrors it
tools/plan_night.py   headless planner (--pass N, --no-mock)
tools/mock_observe.py simulated observations for testing the loop
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
