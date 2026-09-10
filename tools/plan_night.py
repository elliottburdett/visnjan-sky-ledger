"""Plan one night headlessly — same algorithm as the web planner, astropy ephemeris.

    python tools/plan_night.py 2026-09-12 [--end 01:00] [--min-alt 40]
                               [--moon-keepout 30] [--exp 30] [--efficiency 100]

Writes data/nights/night_<date>.json, skipping tiles already complete in
data/coverage.json. The region is grown outward from a seed so the night lands
as one compact block rather than a thin meridian stripe.
"""
import argparse, json
from pathlib import Path
import numpy as np
from astropy.time import Time
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_sun, get_body
import astropy.units as u
from grid import build_grid, ang_sep, SITE, STEP_DEC

ROOT = Path(__file__).resolve().parent.parent
LOC = EarthLocation(lat=SITE["lat"]*u.deg, lon=SITE["lon"]*u.deg, height=SITE["height_m"]*u.m)
TZ = 2  # Europe/Zagreb summer offset; adjust for winter dates


def dark_start(date):
    t0 = Time(f"{date}T13:00:00")
    ts = t0 + np.arange(0, 17*60, 1)*u.min
    alt = get_sun(ts).transform_to(AltAz(obstime=ts, location=LOC)).alt.deg
    for i in range(1, len(alt)):
        if alt[i-1] >= -18 and alt[i] < -18:
            return ts[i]
    return None


def covered_map():
    p = ROOT/"data"/"coverage.json"
    if not p.exists():
        return {}
    m = {}
    for n in json.loads(p.read_text()).get("nights", []):
        if n.get("status") != "observed":
            continue
        for rec in n.get("observed", []):
            i = rec["id"] if isinstance(rec, dict) else rec
            f = len(rec.get("filters", ["G", "R"])) if isinstance(rec, dict) else 2
            m[i] = min(2, m.get(i, 0) + f)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--end", default="01:00")
    ap.add_argument("--min-alt", type=float, default=40)
    ap.add_argument("--moon-keepout", type=float, default=30)
    ap.add_argument("--exp", type=float, default=30)
    ap.add_argument("--efficiency", type=float, default=100)
    a = ap.parse_args()

    dusk = dark_start(a.date)
    if dusk is None:
        raise SystemExit("no astronomical darkness on this date")
    hh, mm = map(int, a.end.split(":"))
    y, mo, d = map(int, a.date.split("-"))
    end = Time(f"{a.date}T00:00:00") + (1*u.day) + (hh - TZ)*u.hour + mm*u.min
    window_min = (end - dusk).sec / 60
    if window_min <= 0:
        raise SystemExit("end time falls before astronomical dark")

    capacity = int(window_min * 60 / (2 * a.exp / (a.efficiency / 100)))
    mid = dusk + (end - dusk) / 2
    cov = covered_map()

    tiles = build_grid()
    ra = np.array([t["ra"] for t in tiles]); dec = np.array([t["dec"] for t in tiles])
    alt = SkyCoord(ra=ra*u.deg, dec=dec*u.deg).transform_to(AltAz(obstime=mid, location=LOC)).alt.deg
    moon = get_body("moon", mid)
    m_alt = moon.transform_to(AltAz(obstime=mid, location=LOC)).alt.deg
    sep = ang_sep(ra, dec, moon.ra.deg, moon.dec.deg)
    sun = get_sun(mid)
    illum = (1 - np.cos(np.radians(sun.separation(moon).deg))) / 2

    ok = np.array([cov.get(t["id"], 0) < 2 for t in tiles]) & (alt >= a.min_alt)
    if m_alt > -2 and a.moon_keepout > 0:
        ok &= sep >= a.moon_keepout
    idx = np.where(ok)[0]
    if not len(idx):
        raise SystemExit("nothing eligible tonight")

    seed = idx[np.argmax(alt[idx])]
    d_seed = ang_sep(ra[idx], dec[idx], ra[seed], dec[seed])
    chosen = idx[np.argsort(d_seed)][:capacity]

    # serpentine by declination row
    rel = ((ra[chosen] - ra[seed] + 180) % 360) - 180
    rows = {}
    for k, i in enumerate(chosen):
        rows.setdefault(tiles[i]["row"], []).append((rel[k], i))
    order = []
    for j, r in enumerate(sorted(rows)):
        arr = sorted(rows[r], key=lambda p: p[0], reverse=bool(j % 2))
        order += [i for _, i in arr]

    out = {"date": a.date, "status": "planned",
           "planned": [tiles[i]["id"] for i in order], "observed": [],
           "window": {"dark": dusk.isot + "Z", "end": end.isot + "Z", "minutes": round(window_min)},
           "params": {"expSec": a.exp, "minAlt": a.min_alt,
                      "efficiency": a.efficiency, "moonKeepOut": a.moon_keepout},
           "moon": {"illum": round(float(illum), 3), "alt": round(float(m_alt), 1),
                    "minSep": round(float(sep[order].min()), 1)},
           "region": {"centreRa": round(float(ra[seed]), 4), "centreDec": round(float(dec[seed]), 4)}}

    p = ROOT/"data"/"nights"/f"night_{a.date}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2))
    print(f"{a.date}: dark {(dusk + TZ*u.hour).iso[11:16]} -> {a.end}, {round(window_min)} min")
    print(f"  {len(order)} tiles, moon {illum*100:.0f}% {'up' if m_alt > 0 else 'down'}, "
          f"nearest {sep[order].min():.0f}deg")
    print(f"  dec {dec[order].min():+.1f}..{dec[order].max():+.1f}, wrote {p}")


if __name__ == "__main__":
    main()
