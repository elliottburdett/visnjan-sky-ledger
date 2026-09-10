"""Telescope definitions and tile grids for the survey.

Single source of truth: the JavaScript in index.html implements exactly the same
TELESCOPES table and grid algorithm, so tile ids match between the web planner
and these tools. Coverage is tracked per telescope — a tile id only means
something alongside the telescope key it was observed with.
"""
import numpy as np

TELESCOPES = {
    "visnjan": {
        "label": "Višnjan — Tičan",
        "site": {"lat": 45.29082, "lon": 13.74926, "height_m": 300, "tz": "Europe/Zagreb"},
        "optics": "wide-field imager",
        "camera": "—",
        "fov_ra": 2.45, "fov_dec": 1.63, "overlap": 0.10,
        "pixel_scale": None,
        "filters": ["G", "R"],
        "filter_positions": {"G": 2, "R": 1},
        "exposure_s": 30,
        "dec_min": -20.0, "dec_max": 90.0,
        "min_alt": 40.0,
        "photometric": True,
        "note": "Antlia LRGB set; G and R used. Filter wheel positions inferred, verify in NINA.",
    },
    "gochile-got1": {
        "label": "GoChile GoT1 — El Sauce",
        "site": {"lat": -30.4725, "lon": -70.7631, "height_m": 1525, "tz": "America/Santiago"},
        "optics": "400 mm f/6.5 Ritchey–Chrétien, 2600 mm focal length",
        "camera": "IMX455 mono, 36.0 × 24.0 mm, 3.76 µm pixels (9576 × 6388)",
        "fov_ra": 0.793, "fov_dec": 0.529, "overlap": 0.10,
        "pixel_scale": 0.30,          # arcsec/pixel
        "filters": ["g", "r"],         # SLOAN/SDSS photometric
        "filter_positions": {},        # not a NINA rig; filled in if it becomes one
        "exposure_s": 30,
        "dec_min": -90.0, "dec_max": 20.0,
        "min_alt": 40.0,
        "photometric": True,
        "note": "FOV derived from aperture x f-ratio and the named sensor; assumes no reducer.",
    },
    "gochile-got2": {
        "label": "GoChile GoT2 — El Sauce (wide field)",
        "site": {"lat": -30.4725, "lon": -70.7631, "height_m": 1525, "tz": "America/Santiago"},
        "optics": "72 mm f/5.6 refractor, 403 mm focal length",
        "camera": "IMX571 colour, 23.5 × 15.7 mm, 3.76 µm pixels (6248 × 4176)",
        "fov_ra": 3.338, "fov_dec": 2.231, "overlap": 0.10,
        "pixel_scale": 1.92,
        "filters": ["L"],              # plus duo-band Ha/OIII
        "filter_positions": {},
        "exposure_s": 30,
        "dec_min": -90.0, "dec_max": 20.0,
        "min_alt": 40.0,
        "photometric": False,
        "note": ("One-shot colour sensor with L and duo-band Ha/OIII only — no Sloan g'/r'. "
                 "Four times the survey speed of GoT1 but cannot do the two-band photometry, "
                 "so it is defined here for reference rather than used."),
    },
}

DEFAULT = "visnjan"

# --- backwards-compatible module-level defaults (Višnjan) ---
SITE = TELESCOPES[DEFAULT]["site"]
FOV_RA = TELESCOPES[DEFAULT]["fov_ra"]
FOV_DEC = TELESCOPES[DEFAULT]["fov_dec"]
OVERLAP = TELESCOPES[DEFAULT]["overlap"]
STEP_RA = FOV_RA * (1 - OVERLAP)
STEP_DEC = FOV_DEC * (1 - OVERLAP)
DEC_MIN = TELESCOPES[DEFAULT]["dec_min"]
TILE_AREA = STEP_RA * STEP_DEC


def steps(scope=DEFAULT):
    t = TELESCOPES[scope]
    return t["fov_ra"] * (1 - t["overlap"]), t["fov_dec"] * (1 - t["overlap"])


def tile_area(scope=DEFAULT):
    sr, sd = steps(scope)
    return sr * sd


def build_grid(scope=DEFAULT, dec_min=None, dec_max=None):
    """Rings of constant declination, evenly spaced in RA, no seam."""
    t = TELESCOPES[scope]
    sr, sd = steps(scope)
    lo = t["dec_min"] if dec_min is None else dec_min
    hi = t["dec_max"] if dec_max is None else dec_max
    tiles, row, dec = [], 0, lo
    while dec <= min(hi, 89.5):
        n = max(1, int(round(360.0 * np.cos(np.radians(dec)) / sr)))
        for c in range(n):
            tiles.append({"id": f"{row}_{c}", "ra": c * 360.0 / n, "dec": dec,
                          "row": row, "col": c, "telescope": scope})
        row += 1
        dec = lo + row * sd
    return tiles


def reachable_dec_floor(scope=DEFAULT, min_alt=None):
    """Lowest declination that ever reaches min_alt from this site."""
    t = TELESCOPES[scope]
    a = t["min_alt"] if min_alt is None else min_alt
    return t["site"]["lat"] - (90 - a)


def reachable_dec_ceiling(scope=DEFAULT, min_alt=None):
    t = TELESCOPES[scope]
    a = t["min_alt"] if min_alt is None else min_alt
    return t["site"]["lat"] + (90 - a)


def ang_sep(ra1, dec1, ra2, dec2):
    a, b = np.radians(dec1), np.radians(dec2)
    d = np.radians(ra1 - ra2)
    return np.degrees(np.arccos(np.clip(np.sin(a)*np.sin(b) + np.cos(a)*np.cos(b)*np.cos(d), -1, 1)))


if __name__ == "__main__":
    print(f"{'telescope':16s} {'FOV (deg)':>15s} {'tile':>7s} {'tiles':>7s} {'area':>9s}  reachable dec")
    for k, t in TELESCOPES.items():
        g = build_grid(k)
        lo = max(reachable_dec_floor(k), t["dec_min"])
        hi = min(reachable_dec_ceiling(k), t["dec_max"])
        flag = "" if t["photometric"] else "   (not photometric)"
        print(f"{k:16s} {t['fov_ra']:6.3f}x{t['fov_dec']:5.3f} {tile_area(k):7.3f} "
              f"{len(g):7d} {len(g)*tile_area(k):8.0f}  {lo:+6.1f}..{hi:+6.1f}{flag}")
