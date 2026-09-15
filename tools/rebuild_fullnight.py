"""Rebuild the 'fullnight' sequence on a new tile list, adding guiding.

    python tools/rebuild_fullnight.py plan.json --donor <fullnight.json> --out new.json

The donor file is the shape specification: its Start and End areas, its Targets
conditions and triggers, and one of its panels are copied verbatim. Only the
Targets item list is rebuilt -

    Wait Until Safe
    Run Autofocus
    <af-every> panels
    Run Autofocus
    <af-every> panels
    ...

and each panel is the donor's panel with new coordinates, a new name, and the
two guider instructions added around the slew:

    prep    Stop Guiding -> Slew (abort) -> Center (abort) -> Start Guiding
    imaging Switch G -> expose -> Switch R -> expose

Slew and Center each stop the guider and restart it themselves if it was
running, so leaving it up across a tile boundary costs two settles. Stopping it
before the slew and starting it after the solve gives one, on the final
pointing, covering both exposures. Both guider instructions continue on error:
a field with no usable guide star should cost one unguided tile, not the night.

--sun-altitude adds a Sun Altitude condition to the Targets container, so the
list loops only while the sun is below the given altitude. NINA stores this as
Comparator GREATER_THAN with the altitude in Offset, which reads backwards but
is right: Check() fails when the current altitude is ABOVE the offset, so
GREATER_THAN is the one that keeps the loop running while it is darker than the
threshold. Conditions SKIP rather than wait, so this ends the night at dawn -
waiting for dark at the start is the Start area's Wait For Time job.
"""
import argparse, copy, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid import build_grid

SEQ = ", NINA.Sequencer"

_counter = [0]
def nid():
    _counter[0] += 1
    return str(_counter[0])


def seed_counter(doc):
    top = [0]
    def walk(o):
        if isinstance(o, dict):
            v = o.get("$id")
            if isinstance(v, str) and v.isdigit():
                top[0] = max(top[0], int(v))
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(doc)
    _counter[0] = top[0] + 1000


def fresh_ids(node):
    """Deep-copy a subtree, giving every $id a new value and repointing the
    $refs that live inside it. Refs out of the subtree are left alone."""
    node = copy.deepcopy(node)
    mapping = {}
    def assign(o):
        if isinstance(o, dict):
            if "$id" in o:
                new = nid(); mapping[o["$id"]] = new; o["$id"] = new
            for k, v in o.items():
                if k != "$id":
                    assign(v)
        elif isinstance(o, list):
            for x in o:
                assign(x)
    def remap(o):
        if isinstance(o, dict):
            if "$ref" in o and o["$ref"] in mapping:
                o["$ref"] = mapping[o["$ref"]]
            for v in o.values():
                remap(v)
        elif isinstance(o, list):
            for x in o:
                remap(x)
    assign(node); remap(node)
    return node


def renumber(doc):
    mapping, counter = {}, [1]
    def assign(o):
        if isinstance(o, list):
            for x in o: assign(x)
        elif isinstance(o, dict):
            if "$id" in o:
                mapping[o["$id"]] = str(counter[0]); o["$id"] = str(counter[0]); counter[0] += 1
            for k, v in o.items():
                if k != "$id": assign(v)
    def remap(o):
        if isinstance(o, list):
            for x in o: remap(x)
        elif isinstance(o, dict):
            if "$ref" in o and o["$ref"] in mapping: o["$ref"] = mapping[o["$ref"]]
            for v in o.values(): remap(v)
    assign(doc); remap(doc)
    return doc


def radec_parts(ra_deg, dec_deg):
    ra_h = ra_deg / 15.0
    h = int(ra_h); m = int((ra_h - h) * 60); s = round((((ra_h - h) * 60) - m) * 60, 5)
    neg = dec_deg < 0; ad = abs(dec_deg)
    d = int(ad); dm = int((ad - d) * 60); ds = round((((ad - d) * 60) - dm) * 60, 5)
    return {"RAHours": h, "RAMinutes": m, "RASeconds": s,
            "NegativeDec": bool(neg), "DecDegrees": d, "DecMinutes": dm, "DecSeconds": ds}


def find(doc, name):
    out = []
    def w(o):
        if isinstance(o, dict):
            if o.get("Name") == name: out.append(o)
            for v in o.values(): w(v)
        elif isinstance(o, list):
            for v in o: w(v)
    w(doc)
    return out[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--donor", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--af-every", type=int, default=30)
    ap.add_argument("--name", default=None)
    ap.add_argument("--no-guide", dest="guide", action="store_false")
    ap.add_argument("--sun-altitude", type=float, default=None,
                    help="add a Sun Altitude condition, e.g. -18 for astronomical dark")
    a = ap.parse_args()

    plan = json.loads(Path(a.plan).read_text())
    doc = json.loads(Path(a.donor).read_text())
    seed_counter(doc)

    targets = find(doc, "Targets")
    items = targets["Items"]["$values"]
    proto_wait = next(x for x in items if "WaitUntilSafe" in x["$type"])
    proto_af = next(x for x in items if "RunAutofocus" in x["$type"])
    proto_panel = next(x for x in items if "DeepSkyObjectContainer" in x["$type"])
    tid = targets["$id"]

    by_id = {t["id"]: t for t in build_grid(plan.get("telescope", "visnjan"))}
    tiles = [by_id[i] for i in plan["planned"] if i in by_id]
    if len(tiles) != len(plan["planned"]):
        raise SystemExit("plan references tiles that are not in the grid")

    def panel(tile, n):
        p = fresh_ids(proto_panel)
        p["Parent"] = {"$ref": tid}
        co = radec_parts(tile["ra"], tile["dec"])
        name = f"T{n:03d}"
        p["Name"] = name
        p["Target"]["TargetName"] = name
        p["Target"]["InputCoordinates"].update(co)
        prep = p["Items"]["$values"][0]
        for it in prep["Items"]["$values"]:
            if "Coordinates" in it:
                it["Coordinates"].update(co)
        if a.guide:
            pid = prep["$id"]
            prep["Items"]["$values"] = (
                [{"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Guider.StopGuiding" + SEQ,
                  "Parent": {"$ref": pid}, "ErrorBehavior": 0, "Attempts": 1}]
                + prep["Items"]["$values"]
                + [{"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Guider.StartGuiding" + SEQ,
                    "ForceCalibration": False, "Parent": {"$ref": pid},
                    "ErrorBehavior": 0, "Attempts": 1}])
        return p

    out = [fresh_ids(proto_wait)]
    out[0]["Parent"] = {"$ref": tid}
    for k, tile in enumerate(tiles):
        if k % a.af_every == 0:
            af = fresh_ids(proto_af); af["Parent"] = {"$ref": tid}
            out.append(af)
        out.append(panel(tile, k + 1))
    targets["Items"]["$values"] = out

    if a.sun_altitude is not None:
        conds = targets["Conditions"]["$values"]
        conds[:] = [c for c in conds if "SunAltitudeCondition" not in c["$type"]]
        conds.append({
            "$id": nid(),
            "$type": "NINA.Sequencer.Conditions.SunAltitudeCondition" + SEQ,
            "Data": {
                "$id": nid(),
                "$type": "NINA.Sequencer.SequenceItem.Utility.WaitLoopData" + SEQ,
                "Coordinates": {
                    "$id": nid(),
                    "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry",
                    "RAHours": 0, "RAMinutes": 0, "RASeconds": 0.0,
                    "NegativeDec": False, "DecDegrees": 0, "DecMinutes": 0, "DecSeconds": 0.0},
                "Offset": float(a.sun_altitude),
                # 3 = GREATER_THAN; the enum serialises as an int, no
                # StringEnumConverter in NINA's SequenceJsonConverter
                "Comparator": 3},
            "Parent": {"$ref": tid}})

    if a.name:
        doc["Name"] = a.name
    renumber(doc)

    p = Path(a.out); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2))

    ids, refs = [], []
    def w(o):
        if isinstance(o, dict):
            if "$id" in o: ids.append(o["$id"])
            if "$ref" in o and len(o) == 1: refs.append(o["$ref"])
            for k, v in o.items():
                if k != "$id": w(v)
        elif isinstance(o, list):
            for x in o: w(x)
    w(doc)
    assert len(ids) == len(set(ids)), "duplicate $id"
    dangling = [r for r in refs if r not in set(ids)]
    assert not dangling, f"dangling $ref: {dangling[:5]}"
    naf = sum(1 for x in targets["Items"]["$values"] if "RunAutofocus" in x["$type"])
    names = [c["$type"].split(",")[0].rsplit(".", 1)[-1] for c in targets["Conditions"]["$values"]]
    print(f"{p}  —  {len(tiles)} tiles, {len(tiles)*2} exposures, {naf} autofocus runs, "
          f"guiding {'on' if a.guide else 'off'}, {len(ids)} unique ids, 0 dangling refs")
    print(f"     Targets conditions: {', '.join(names)}")


if __name__ == "__main__":
    main()
