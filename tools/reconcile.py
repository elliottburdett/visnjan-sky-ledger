"""Fold the frames NINA actually wrote into the survey ledger.

    python tools/reconcile.py --fits "/path/to/2026-09-15/**/*.fits"
    python tools/reconcile.py --csv frames.csv          # ra_deg,dec_deg,filter[,date_obs]
    python tools/reconcile.py --rebuild                 # recompute coverage only

Two layers, deliberately:

  data/frames/frames_<date>_<telescope>.csv   the RAW record, append-only
  data/coverage.json                          DERIVED, rebuilt from the above

Everything in coverage.json can be thrown away and regenerated, so a bad tile
match or a changed grid is a re-run rather than a loss. The frame files are the
thing to back up; they hold one row per exposure, straight out of the headers.

NINA does not keep an observation database - its own logs
(%LOCALAPPDATA%\\NINA\\Logs) are Serilog diagnostics, not a catalogue. What it
does write, on every frame, is a full FITS header. The keywords that matter:

  OBJECT             the panel name from the sequence
  OBJCTRA/OBJCTDEC   the TARGET coordinates, sexagesimal - the tile centre
  RA/DEC             where the mount actually was, in degrees
  CRVAL1/CRVAL2      solved centre, present only on plate-solved frames
  FILTER             active filter
  DATE-OBS           exposure start, UTC
  EXPOSURE/EXPTIME, IMAGETYP, CENTALT, AIRMASS, FOCALLEN, INSTRUME, TELESCOP

Pointing is taken in that order of trust: solved WCS, then the target
coordinates, then the mount. A frame whose OBJECT parses as a coordinate name
(RA221235_Dec+300000_G, the blocks scheme) is matched to its tile exactly, with
no nearest-neighbour search at all - that is the argument for those names over
T001. Everything else falls back to nearest tile within --tol degrees.
"""
import argparse, csv, glob, json, re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
from grid import build_grid, ang_sep, TELESCOPES, DEFAULT

ROOT = Path(__file__).resolve().parent.parent
FRAME_COLS = ["ra_deg", "dec_deg", "filter", "date_obs", "exptime_s",
              "object", "source", "tile_hint", "mock"]
NAME_RE = re.compile(r"^RA(\d\d)(\d\d)(\d\d)_Dec([+-])(\d\d)(\d\d)(\d\d)_([A-Za-z]+)$")


# ---------------------------------------------------------------- reading ---
def _sexa(v):
    """'22 12 35.40' or '22:12:35.40' -> float. Returns None if unparseable."""
    if v is None:
        return None
    parts = re.split(r"[\s:]+", str(v).strip())
    try:
        nums = [float(p) for p in parts if p not in ("", "+")]
    except ValueError:
        return None
    if not nums:
        return None
    sign = -1.0 if str(v).strip().startswith("-") else 1.0
    mag = abs(nums[0]) + (nums[1] if len(nums) > 1 else 0) / 60 + (nums[2] if len(nums) > 2 else 0) / 3600
    return sign * mag


def frames_from_fits(patterns):
    from astropy.io import fits
    out, skipped = [], 0
    for pat in patterns:
        for fn in sorted(glob.glob(pat, recursive=True)):
            try:
                h = fits.getheader(fn)
            except Exception:
                skipped += 1
                continue
            if str(h.get("IMAGETYP", "LIGHT")).upper() not in ("LIGHT", "OBJECT", ""):
                continue
            ra = dec = None
            src = ""
            if h.get("CRVAL1") is not None and h.get("CRVAL2") is not None:
                ra, dec, src = float(h["CRVAL1"]), float(h["CRVAL2"]), "wcs"
            elif h.get("OBJCTRA") is not None and h.get("OBJCTDEC") is not None:
                rh, dd = _sexa(h["OBJCTRA"]), _sexa(h["OBJCTDEC"])
                if rh is not None and dd is not None:
                    ra, dec, src = rh * 15.0, dd, "target"
            if ra is None and h.get("RA") is not None and h.get("DEC") is not None:
                ra, dec, src = float(h["RA"]), float(h["DEC"]), "mount"
            if ra is None:
                skipped += 1
                continue
            out.append({
                "ra_deg": round(float(ra), 6), "dec_deg": round(float(dec), 6),
                "filter": str(h.get("FILTER", "")).strip(),
                "date_obs": str(h.get("DATE-OBS", "")).strip(),
                "exptime_s": h.get("EXPOSURE", h.get("EXPTIME", "")),
                "object": str(h.get("OBJECT", "")).strip(),
                "source": src, "tile_hint": "", "mock": 0,
            })
    return out, skipped


def frames_from_csv(path):
    out = []
    with open(path) as f:
        rows = [ln for ln in f if not ln.lstrip().startswith("#")]
    for row in csv.DictReader(rows):
        if not row.get("ra_deg"):
            continue
        out.append({"ra_deg": float(row["ra_deg"]), "dec_deg": float(row["dec_deg"]),
                    "filter": (row.get("filter") or "").strip(),
                    "date_obs": row.get("date_obs", ""),
                    "exptime_s": row.get("exptime_s", ""),
                    "object": row.get("object", ""),
                    "source": row.get("source", "csv"),
                    "tile_hint": row.get("tile_hint", ""),
                    "mock": int(row.get("mock", 0) or 0)})
    return out


def observing_date(frame):
    """The evening date a frame belongs to: UTC minus 12h, as NINA's
    $$DATEMINUS12$$ pattern does, so a whole night shares one date."""
    s = frame.get("date_obs") or ""
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00").split(".")[0].replace("+00:00", ""))
    except ValueError:
        return None
    return (t - timedelta(hours=12)).date().isoformat()


# --------------------------------------------------------------- matching ---
def match_tiles(frames, scope, tol):
    """Attach a tile id to every frame. Exact by name where the name carries
    coordinates, nearest tile within tol otherwise."""
    tiles = build_grid(scope)
    tra = np.array([t["ra"] for t in tiles]); tdec = np.array([t["dec"] for t in tiles])
    by_name, unmatched, exact = {}, 0, 0
    for fr in frames:
        m = NAME_RE.match(fr.get("object", "") or "")
        if m:
            ra = (int(m.group(1)) + int(m.group(2)) / 60 + int(m.group(3)) / 3600) * 15
            dec = (int(m.group(5)) + int(m.group(6)) / 60 + int(m.group(7)) / 3600)
            if m.group(4) == "-":
                dec = -dec
            exact += 1
        else:
            ra, dec = fr["ra_deg"], fr["dec_deg"]
        d = ang_sep(tra, tdec, ra, dec)
        i = int(np.argmin(d))
        if d[i] <= tol:
            fr["tile_hint"] = tiles[i]["id"]
            by_name[tiles[i]["id"]] = by_name.get(tiles[i]["id"], 0) + 1
        else:
            fr["tile_hint"] = ""
            unmatched += 1
    return by_name, unmatched, exact


# ---------------------------------------------------------------- ledger ----
def frame_key(fr):
    """Identity of a frame, for append-without-duplicating."""
    return (fr.get("date_obs", ""), fr.get("filter", ""),
            round(float(fr["ra_deg"]), 4), round(float(fr["dec_deg"]), 4))


def append_frames(path, frames, header_note=None):
    existing = frames_from_csv(path) if path.exists() else []
    seen = {frame_key(f) for f in existing}
    fresh = [f for f in frames if frame_key(f) not in seen]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        if header_note:
            f.write(f"# {header_note}\n")
        w = csv.DictWriter(f, fieldnames=FRAME_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(existing + fresh)
    return len(fresh), len(existing) + len(fresh)


def rebuild_coverage(tol):
    """coverage.json from every frame file on disk. Purely derived."""
    cov_path = ROOT / "data" / "coverage.json"
    cov = json.loads(cov_path.read_text()) if cov_path.exists() else {}
    nights = []
    files = sorted(glob.glob(str(ROOT / "data" / "frames" / "frames_*.csv"))) + \
            sorted(glob.glob(str(ROOT / "data" / "mock" / "frames_*.csv")))
    for fn in files:
        stem = Path(fn).stem                      # frames_<date>_<scope>
        bits = stem.split("_")
        if len(bits) < 3:
            continue
        date, scope = bits[1], "_".join(bits[2:])
        if scope not in TELESCOPES:
            print(f"  ! {Path(fn).name}: unknown telescope '{scope}', skipped")
            continue
        is_mock = Path(fn).parent.name == "mock"
        frames = frames_from_csv(fn)
        if not frames:
            continue
        if any(not f.get("tile_hint") for f in frames):
            match_tiles(frames, scope, tol)
        per_tile = defaultdict(list)
        for f in frames:
            if f.get("tile_hint"):
                per_tile[f["tile_hint"]].append(f["filter"])
        observed = [{"id": k, "filters": sorted(v)} for k, v in sorted(per_tile.items())]
        need = set(TELESCOPES[scope]["filters"])
        complete = sum(1 for o in observed if need.issubset(set(o["filters"])))
        plan_p = ROOT / "data" / "nights" / f"night_{date}_{scope}.json"
        plan = json.loads(plan_p.read_text()) if plan_p.exists() else {}
        night = {"date": date, "telescope": scope, "status": "observed",
                 "pass": plan.get("pass", 1),
                 "planned": plan.get("planned", []), "observed": observed,
                 "frames": len(frames), "source": Path(fn).name,
                 "note": (("SYNTHETIC — " if is_mock else "")
                          + f"{len(frames)} frames, {len(observed)} tiles touched, "
                            f"{complete} complete in {'+'.join(sorted(need))}")}
        if is_mock:
            night["mock"] = True
        nights.append(night)
    nights.sort(key=lambda n: (n["date"], n["telescope"]))
    cov["nights"] = nights
    cov_path.parent.mkdir(parents=True, exist_ok=True)
    cov_path.write_text(json.dumps(cov, indent=2))
    return cov_path, nights


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=None,
                    help="evening date; inferred from DATE-OBS when omitted")
    ap.add_argument("--telescope", default=DEFAULT, choices=sorted(TELESCOPES))
    ap.add_argument("--fits", nargs="*", default=None, help="glob(s); ** is supported")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--tol", type=float, default=0.4,
                    help="degrees; a frame further than this from every tile is unmatched")
    ap.add_argument("--rebuild", action="store_true",
                    help="skip ingest, just recompute coverage.json from the frame files")
    a = ap.parse_args()

    if not a.rebuild:
        if not (a.fits or a.csv):
            raise SystemExit("give --fits, --csv, or --rebuild")
        frames, skipped = (frames_from_fits(a.fits) if a.fits
                           else (frames_from_csv(a.csv), 0))
        if not frames:
            raise SystemExit("no usable frames found (need a pointing and a filter)")
        keep = set(TELESCOPES[a.telescope]["filters"])
        dropped = [f for f in frames if f["filter"] not in keep]
        frames = [f for f in frames if f["filter"] in keep]
        if not frames:
            raise SystemExit(f"no frames in {sorted(keep)} — found "
                             f"{sorted({f['filter'] for f in dropped})}")

        by_date = defaultdict(list)
        for f in frames:
            by_date[a.date or observing_date(f) or "unknown"].append(f)
        if "unknown" in by_date and not a.date:
            raise SystemExit("frames have no readable DATE-OBS — pass the date explicitly")

        for date, group in sorted(by_date.items()):
            _, unmatched, exact = match_tiles(group, a.telescope, a.tol)
            p = ROOT / "data" / "frames" / f"frames_{date}_{a.telescope}.csv"
            new, total = append_frames(p, group)
            print(f"{date} [{a.telescope}]: {len(group)} frames read, {new} new, "
                  f"{total} in ledger")
            print(f"  {exact} matched by name, {unmatched} matched no tile within {a.tol}°")
            if dropped:
                print(f"  {len(dropped)} frames skipped: filter not in {sorted(keep)}")
            if skipped:
                print(f"  {skipped} files skipped: unreadable or no pointing")
            print(f"  wrote {p}")

    cov_path, nights = rebuild_coverage(a.tol)
    real = [n for n in nights if not n.get("mock")]
    print(f"\ncoverage rebuilt from {len(nights)} frame file(s) "
          f"({len(real)} real, {len(nights)-len(real)} synthetic) -> {cov_path}")
    for n in nights:
        print(f"  {n['date']} {n['telescope']:<14} {n['note']}")


if __name__ == "__main__":
    main()
