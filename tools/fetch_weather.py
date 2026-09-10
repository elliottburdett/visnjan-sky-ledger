"""Refresh data/weather.json from met.no for the Višnjan site.

Usage:  python tools/fetch_weather.py [nights_ahead]
met.no requires a descriptive User-Agent; set WEATHER_UA to your own contact.
"""
import json, os, sys, urllib.request, datetime as dt
from pathlib import Path
from grid import SITE

UA = os.environ.get("WEATHER_UA", "visnjan-sky-ledger/1.0 (github.com/USER/visnjan-sky-ledger)")
URL = (f"https://api.met.no/weatherapi/locationforecast/2.0/compact"
       f"?lat={SITE['lat']:.5f}&lon={SITE['lon']:.5f}")
DARK_HOURS_UTC = [19, 20, 21, 22, 23]   # roughly the dark window in September


def main(nights=5):
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        js = json.load(r)

    by_hour = {}
    for e in js["properties"]["timeseries"]:
        d = e["data"]["instant"]["details"]
        by_hour[e["time"][:13]] = {
            "cloud": round(d.get("cloud_area_fraction", 0)),
            "rh": round(d.get("relative_humidity", 0)),
            "temp": d.get("air_temperature"),
            "wind": d.get("wind_speed"),
        }

    out, today = {}, dt.date.today()
    for i in range(nights):
        day = today + dt.timedelta(days=i)
        hours = []
        for h in DARK_HOURS_UTC:
            key = f"{day.isoformat()}T{h:02d}"
            if key in by_hour:
                rec = by_hour[key]
                hours.append({"t": f"{(h+2)%24:02d}:00", "cloud": rec["cloud"], "rh": rec["rh"]})
        if hours:
            out[day.isoformat()] = {"fetched": today.isoformat() + " (met.no)",
                                    "source": "api.met.no locationforecast 2.0",
                                    "hours": hours}

    p = Path(__file__).resolve().parent.parent / "data" / "weather.json"
    p.write_text(json.dumps(out, indent=2))
    for d, w in out.items():
        peak = max(h["cloud"] for h in w["hours"])
        print(f"{d}  peak cloud {peak:3d}%  ->  {'GO' if peak <= 25 else 'marginal' if peak <= 60 else 'NO GO'}")
    print(f"wrote {p}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
