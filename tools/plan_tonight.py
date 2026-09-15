"""Plan a full dark night on sky a given NINA file did not touch.

    python tools/plan_tonight.py 2026-09-15 --avoid <file.json> --end 05:00

Differs from plan_night.py in two ways, both because a full dark window is
eight hours rather than four:

  * Altitude is evaluated at the time each tile is actually scheduled, not once
    at mid-window. A compact region cannot stay above 40 deg for eight hours -
    picking tiles by mid-window altitude would point a third of the night at
    sky that has already set.
  * Tiles are chosen by nearest-neighbour from the current pointing among those
    presently high enough, so the region walks west to east with the sky.

Exclusion is by FOOTPRINT, not tile id: the avoided file's panels sit on a grid
with a different RA phase, so a tile is ruled out when its frame would overlap
a covered frame at all - |d dec| < fov_dec and |d RA . cos dec| < fov_ra.
"""
import argparse, json
from pathlib import Path
import numpy as np
from astropy.time import Time
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_sun, get_body
import astropy.units as u
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid import build_grid, ang_sep, tile_area, TELESCOPES, DEFAULT
from plan_night import tz_offset_hours, dark_start

ROOT = Path(__file__).resolve().parent.parent
SLICE_MIN = 15          # altitude is resampled this often



def panel_centres(path):
    """Every panel pointing in a NINA file, as (ra, dec) in degrees."""
    doc = json.loads(Path(path).read_text())
    pts = []

    def walk(o):
        if isinstance(o, dict):
            if "DeepSkyObjectContainer" in str(o.get("$type", "")):
                c = o["Target"]["InputCoordinates"]
                ra = (c["RAHours"] + c["RAMinutes"]/60 + c["RASeconds"]/3600) * 15
                dec = (c["DecDegrees"] + c["DecMinutes"]/60 + c["DecSeconds"]/3600)
                if c["NegativeDec"]:
                    dec = -dec
                pts.append((ra, dec))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(doc)
    return sorted(set(pts))


def overlapping(tiles, pts, fov_ra, fov_dec):
    """Ids of tiles whose frame would overlap any covered frame."""
    if not pts:
        return set()
    tra = np.array([t["ra"] for t in tiles]); tdec = np.array([t["dec"] for t in tiles])
    hit = np.zeros(len(tiles), bool)
    for ra, dec in pts:
        ddec = np.abs(tdec - dec)
        dra = np.abs(((tra - ra + 180) % 360) - 180) * np.cos(np.radians((tdec + dec) / 2))
        hit |= (ddec < fov_dec) & (dra < fov_ra)
    return {tiles[i]["id"] for i in np.where(hit)[0]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--avoid", action="append", default=[])
    ap.add_argument("--telescope", default=DEFAULT, choices=sorted(TELESCOPES))
    ap.add_argument("--end", default="05:00")
    ap.add_argument("--min-alt", type=float, default=None)
    ap.add_argument("--moon-keepout", type=float, default=30)
    ap.add_argument("--exp", type=float, default=30)
    ap.add_argument("--settle", type=float, default=10)
    ap.add_argument("--settles-per-tile", type=float, default=2,
                    help="2 when each filter is its own panel, 1 when both "
                         "exposures sit under one guide session")
    ap.add_argument("--efficiency", type=float, default=100)
    ap.add_argument("--af-block", type=int, default=30,
                    help="trim the tile count to a whole number of filter blocks")
    a = ap.parse_args()

    spec = TELESCOPES[a.telescope]; site = spec["site"]
    LOC = EarthLocation(lat=site["lat"]*u.deg, lon=site["lon"]*u.deg, height=site["height_m"]*u.m)
    TZ = tz_offset_hours(site["tz"], a.date)
    min_alt = spec["min_alt"] if a.min_alt is None else a.min_alt

    dusk = dark_start(a.date, LOC)
    if dusk is None:
        raise SystemExit("no astronomical darkness on this date")
    hh, mm = map(int, a.end.split(":"))
    end = Time(f"{a.date}T00:00:00") + (1*u.day) + (hh - TZ)*u.hour + mm*u.min
    window_min = (end - dusk).sec / 60
    if window_min <= 0:
        raise SystemExit("end time falls before astronomical dark")

    tiles = build_grid(a.telescope)
    ra = np.array([t["ra"] for t in tiles]); dec = np.array([t["dec"] for t in tiles])

    avoid, covered = set(), []
    for f in a.avoid:
        pts = panel_centres(f)
        hit = overlapping(tiles, pts, spec["fov_ra"], spec["fov_dec"])
        print(f"{Path(f).name}: {len(pts)} pointings -> {len(hit)} tiles ruled out by frame overlap")
        avoid |= hit; covered += pts

    # altitude on a coarse time grid, interpolated by nearest slice
    nslice = int(window_min // SLICE_MIN) + 2
    times = dusk + np.arange(nslice) * SLICE_MIN * u.min
    sky = SkyCoord(ra=ra*u.deg, dec=dec*u.deg)
    altg = np.vstack([sky.transform_to(AltAz(obstime=t, location=LOC)).alt.deg for t in times])

    mid = dusk + (end - dusk) / 2
    moon = get_body("moon", mid)
    m_alt = moon.transform_to(AltAz(obstime=mid, location=LOC)).alt.deg
    sep = ang_sep(ra, dec, moon.ra.deg, moon.dec.deg)
    sun = get_sun(mid)
    illum = (1 - np.cos(np.radians(sun.separation(moon).deg))) / 2

    base = np.array([t["id"] not in avoid for t in tiles])
    if m_alt > -2 and a.moon_keepout > 0:
        base &= sep >= a.moon_keepout

    # two exposures per tile; guiding settles once per panel, and how many
    # panels a tile becomes depends on the sequence scheme
    per_tile = (2 * a.exp + a.settles_per_tile * a.settle) / (a.efficiency / 100)
    capacity = int(window_min * 60 / per_tile)

    used = np.zeros(len(tiles), bool)
    order, alts = [], []
    elapsed = 0.0
    cur = None
    while len(order) < capacity and elapsed < window_min * 60:
        k = min(int(elapsed / 60 / SLICE_MIN), nslice - 1)
        ok = base & ~used & (altg[k] >= min_alt)
        idx = np.where(ok)[0]
        if not len(idx):
            break
        if cur is None:                       # seed on the highest tile at dark
            pick = idx[np.argmax(altg[k][idx])]
        else:                                 # then the nearest one still high enough
            pick = idx[np.argmin(ang_sep(ra[idx], dec[idx], ra[cur], dec[cur]))]
        used[pick] = True
        order.append(int(pick)); alts.append(float(altg[k][pick]))
        cur = pick
        elapsed += per_tile

    if a.af_block > 1:
        keep = (len(order) // a.af_block) * a.af_block
        if keep and keep != len(order):
            print(f"  trimming {len(order)} -> {keep} tiles for whole filter blocks")
            order, alts = order[:keep], alts[:keep]

    assert not (set(tiles[i]["id"] for i in order) & avoid), "picked an excluded tile"
    assert len(set(order)) == len(order), "duplicate tile in the plan"
    if covered:
        cra = np.array([p[0] for p in covered]); cdec = np.array([p[1] for p in covered])
        for i in order:
            ddec = np.abs(cdec - dec[i])
            dra = np.abs(((cra - ra[i] + 180) % 360) - 180) * np.cos(np.radians((cdec + dec[i]) / 2))
            assert not np.any((ddec < spec["fov_dec"]) & (dra < spec["fov_ra"])), \
                f"planned tile {tiles[i]['id']} overlaps covered sky"
        nearest = min(float(np.min(ang_sep(cra, cdec, ra[i], dec[i]))) for i in order)
    else:
        nearest = None

    slews = [float(ang_sep(ra[order[k]], dec[order[k]], ra[order[k+1]], dec[order[k+1]]))
             for k in range(len(order)-1)]
    med = float(np.median(slews)) if slews else 0.0

    out = {"date": a.date, "telescope": a.telescope, "status": "planned", "pass": 1,
           "planned": [tiles[i]["id"] for i in order], "observed": [],
           "window": {"dark": dusk.isot + "Z", "end": end.isot + "Z", "minutes": round(window_min)},
           "params": {"expSec": a.exp, "minAlt": min_alt, "efficiency": a.efficiency,
                      "moonKeepOut": a.moon_keepout, "guide": True, "settleSec": a.settle,
                      "settlesPerTile": a.settles_per_tile},
           "moon": {"illum": round(float(illum), 3), "alt": round(float(m_alt), 1),
                    "minSep": round(float(sep[order].min()), 1)},
           "region": {"centreRa": round(float(np.mean(ra[order])), 4),
                      "centreDec": round(float(np.mean(dec[order])), 4),
                      "medianSlewDeg": round(med, 3)},
           "avoidedTiles": len(avoid)}
    p = ROOT/"data"/"nights"/f"night_{a.date}_{a.telescope}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2))

    print(f"{a.date} [{a.telescope}]: dark {(dusk + TZ*u.hour).iso[11:16]} -> {a.end} local, "
          f"{round(window_min)} min, capacity {capacity}")
    print(f"  {len(order)} tiles, {len(order)*2} panels, "
          f"{len(order)*tile_area(a.telescope):.0f} deg^2")
    print(f"  altitude when scheduled: min {min(alts):.1f}, median {np.median(alts):.1f} deg")
    print(f"  RA {ra[order].min():.1f}..{ra[order].max():.1f}, "
          f"dec {dec[order].min():+.1f}..{dec[order].max():+.1f}")
    print(f"  median slew {med:.2f} deg, 90th pct {np.percentile(slews,90):.2f} deg")
    print(f"  moon {illum*100:.0f}% {'up' if m_alt > 0 else 'down'}, nearest {sep[order].min():.0f} deg"
          + (f", nearest covered sky {nearest:.2f} deg" if nearest else ""))
    print(f"  wrote {p}")


if __name__ == "__main__":
    main()
