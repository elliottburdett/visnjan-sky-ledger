"""Fold a night's actual frames into data/coverage.json.

The ledger records what was OBSERVED, not what was planned, so this reads the
frames NINA actually wrote and matches each to its tile by coordinates.

    python tools/reconcile.py 2026-09-12 --fits /path/to/night/*.fits
    python tools/reconcile.py 2026-09-12 --csv frames.csv    # ra_deg,dec_deg,filter

A frame counts for a tile if its centre lands within --tol degrees (default 0.4,
about a quarter of the short field axis). Tiles seen in both G and R are complete.
"""
import argparse, csv, json, glob
from collections import defaultdict
from pathlib import Path
import numpy as np
from grid import build_grid, ang_sep

ROOT = Path(__file__).resolve().parent.parent


def frames_from_fits(patterns):
    from astropy.io import fits
    out = []
    for pat in patterns:
        for fn in sorted(glob.glob(pat)):
            h = fits.getheader(fn)
            ra = h.get("CRVAL1", h.get("RA"))
            dec = h.get("CRVAL2", h.get("DEC"))
            filt = str(h.get("FILTER", "")).strip().upper()[:1]
            if ra is None or dec is None or filt not in ("G", "R"):
                continue
            out.append((float(ra), float(dec), filt))
    return out


def frames_from_csv(path):
    out = []
    with open(path) as f:
        for row in csv.DictReader(f):
            filt = str(row.get("filter", "")).strip().upper()[:1]
            if filt in ("G", "R"):
                out.append((float(row["ra_deg"]), float(row["dec_deg"]), filt))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--fits", nargs="*", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--tol", type=float, default=0.4)
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    frames = frames_from_fits(a.fits) if a.fits else frames_from_csv(a.csv)
    if not frames:
        raise SystemExit("no usable frames found (need RA, Dec and a G/R filter)")

    tiles = build_grid()
    tra = np.array([t["ra"] for t in tiles])
    tdec = np.array([t["dec"] for t in tiles])

    hit, unmatched = defaultdict(set), 0
    for ra, dec, filt in frames:
        d = ang_sep(tra, tdec, ra, dec)
        i = int(np.argmin(d))
        if d[i] <= a.tol:
            hit[tiles[i]["id"]].add(filt)
        else:
            unmatched += 1

    observed = [{"id": k, "filters": sorted(v)} for k, v in sorted(hit.items())]
    both = sum(1 for o in observed if len(o["filters"]) == 2)

    cov_path = ROOT / "data" / "coverage.json"
    cov = json.loads(cov_path.read_text()) if cov_path.exists() else {"nights": []}
    cov["nights"] = [n for n in cov["nights"] if n.get("date") != a.date]

    plan_path = ROOT / "data" / "nights" / f"night_{a.date}.json"
    planned = json.loads(plan_path.read_text()).get("planned", []) if plan_path.exists() else []

    cov["nights"].append({"date": a.date, "status": "observed",
                          "planned": planned, "observed": observed,
                          "note": a.note or f"{len(frames)} frames, {both} tiles in both filters"})
    cov["nights"].sort(key=lambda n: n["date"])
    cov_path.write_text(json.dumps(cov, indent=2))

    print(f"{len(frames)} frames -> {len(observed)} tiles touched, {both} complete in G+R")
    if unmatched:
        print(f"  {unmatched} frames matched no tile within {a.tol}° — check pointing or --tol")
    if planned:
        got = {o['id'] for o in observed}
        print(f"  plan had {len(planned)} tiles; {len(got & set(planned))} of them observed")
    print(f"wrote {cov_path}")


if __name__ == "__main__":
    main()
