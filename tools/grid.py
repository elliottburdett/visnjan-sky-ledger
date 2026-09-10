"""Tile grid for the Višnjan all-sky survey.

Single source of truth: the JavaScript in index.html implements exactly this
algorithm, so tile ids match between the web planner and these tools.
"""
import numpy as np

SITE = dict(lat=45.29082, lon=13.74926, height_m=300, tz="Europe/Zagreb")
FOV_RA, FOV_DEC, OVERLAP = 2.45, 1.63, 0.10
STEP_RA, STEP_DEC = FOV_RA * (1 - OVERLAP), FOV_DEC * (1 - OVERLAP)
DEC_MIN = -20.0
TILE_AREA = STEP_RA * STEP_DEC


def build_grid(dec_min=DEC_MIN):
    """Rings of constant declination, evenly spaced in RA, no seam."""
    tiles, row, dec = [], 0, dec_min
    while dec <= 89.5:
        n = max(1, int(round(360.0 * np.cos(np.radians(dec)) / STEP_RA)))
        for c in range(n):
            tiles.append({"id": f"{row}_{c}", "ra": c * 360.0 / n, "dec": dec,
                          "row": row, "col": c})
        row += 1
        dec = dec_min + row * STEP_DEC
    return tiles


def ang_sep(ra1, dec1, ra2, dec2):
    a, b = np.radians(dec1), np.radians(dec2)
    d = np.radians(ra1 - ra2)
    return np.degrees(np.arccos(np.clip(np.sin(a)*np.sin(b) + np.cos(a)*np.cos(b)*np.cos(d), -1, 1)))


if __name__ == "__main__":
    t = build_grid()
    print(f"{len(t)} tiles, {len(t)*TILE_AREA:.0f} deg^2, dec floor {DEC_MIN:+.0f}")
