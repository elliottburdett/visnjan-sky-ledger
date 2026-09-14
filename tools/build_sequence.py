"""Build a N.I.N.A. Advanced Sequencer file from a saved night plan.

    python tools/build_sequence.py data/nights/night_2026-09-12.json \
           --template templates/observatory_base.json

Takes your own working sequence as the template: its Start and End areas are
copied through byte-for-byte, and only the "Targets" container is replaced.

The tile list is cut into blocks of AF_BLOCK tiles and each block is imaged
once per filter:

    WaitUntilSafe
    Switch G -> RunAutofocus -> 30 panels (block order)
    Switch R -> RunAutofocus -> the same 30 panels, reversed
    Switch G -> RunAutofocus -> next 30 panels ...

so the filter wheel moves twice per block instead of twice per panel, focus is
re-measured on every filter change, and the mount begins each block where the
previous one ended. A panel is one exposure:

    prep    Slew to Ra/Dec (abort on error) -> Center (abort on error)
    imaging TakeExposure

Every SwitchFilter carries its own inline FilterInfo rather than a shared $ref.
Panels carry NO conditions: in NINA a container with conditions REPEATS while
they hold, so a per-panel condition makes each panel loop forever instead of
advancing. Panel names encode the tile centre (RAhhmmss_Dec+ddmmss_F) so frames
can be matched back to tiles from the header alone.

Mirrors the generator in index.html exactly — keep the two in step.
"""
import argparse, json
from pathlib import Path
from grid import build_grid

EXP_TIME = 30.0
AF_BLOCK = 30                          # tiles per filter block = autofocus cadence
FILTER_POSITIONS = {"G": 2, "R": 1}   # verify against Options > Equipment > Filter Wheel

_counter = 999
def nid():
    global _counter
    _counter += 1
    return str(_counter)

def radec_parts(ra_deg, dec_deg):
    ra_h = ra_deg / 15.0
    h = int(ra_h); m = int((ra_h - h) * 60); s = round((((ra_h - h) * 60) - m) * 60, 5)
    neg = dec_deg < 0; ad = abs(dec_deg)
    d = int(ad); dm = int((ad - d) * 60); ds = round((((ad - d) * 60) - dm) * 60, 5)
    return {"RAHours": h, "RAMinutes": m, "RASeconds": s,
            "NegativeDec": bool(neg), "DecDegrees": d, "DecMinutes": dm, "DecSeconds": ds}

def sex_name(ra_deg, dec_deg, filt):
    """RA221235_Dec+300000_G — the tile centre, rounded to whole seconds."""
    rh = ra_deg / 15.0
    h = int(rh); mf = (rh - h) * 60; m = int(mf); s = round((mf - m) * 60)
    if s == 60: s, m = 0, m + 1
    if m == 60: m, h = 0, h + 1
    h %= 24
    neg = dec_deg < 0; ad = abs(dec_deg)
    d = int(ad); df = (ad - d) * 60; dm = int(df); ds = round((df - dm) * 60)
    if ds == 60: ds, dm = 0, dm + 1
    if dm == 60: dm, d = 0, d + 1
    return f"RA{h:02d}{m:02d}{s:02d}_Dec{'-' if neg else '+'}{d:02d}{dm:02d}{ds:02d}_{filt}"

def wait_until_safe(parent):
    return {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.SafetyMonitor.WaitUntilSafe, NINA.Sequencer",
            "Parent": {"$ref": parent}, "ErrorBehavior": 0, "Attempts": 1}

def coll(kind, values):
    t = {"cond": "System.Collections.ObjectModel.ObservableCollection`1[[NINA.Sequencer.Conditions.ISequenceCondition, NINA.Sequencer]], System.ObjectModel",
         "item": "System.Collections.ObjectModel.ObservableCollection`1[[NINA.Sequencer.SequenceItem.ISequenceItem, NINA.Sequencer]], System.ObjectModel",
         "trig": "System.Collections.ObjectModel.ObservableCollection`1[[NINA.Sequencer.Trigger.ISequenceTrigger, NINA.Sequencer]], System.ObjectModel"}[kind]
    return {"$id": nid(), "$type": t, "$values": values}

def make_filter(name, position):
    return {"$id": nid(), "$type": "NINA.Core.Model.Equipment.FilterInfo, NINA.Core",
            "_name": name, "_focusOffset": 0, "_position": position,
            "_autoFocusExposureTime": -1.0, "_autoFocusFilter": False,
            "FlatWizardFilterSettings": {"$id": nid(),
                "$type": "NINA.Core.Model.Equipment.FlatWizardFilterSettings, NINA.Core",
                "FlatWizardMode": 0, "HistogramMeanTarget": 0.4, "HistogramTolerance": 0.1,
                "MaxFlatExposureTime": 3.0, "MinFlatExposureTime": 0.01,
                "MaxAbsoluteFlatDeviceBrightness": 80, "MinAbsoluteFlatDeviceBrightness": 0,
                "Gain": -1, "Offset": -1,
                "Binning": {"$id": nid(), "$type": "NINA.Core.Model.Equipment.BinningMode, NINA.Core", "X": 1, "Y": 1}},
            "_autoFocusBinning": {"$id": nid(), "$type": "NINA.Core.Model.Equipment.BinningMode, NINA.Core", "X": 1, "Y": 1},
            "_autoFocusGain": -1, "_autoFocusOffset": -1}

def switch_filter(parent, name):
    """A fresh, fully inline FilterInfo every time — deliberately not shared."""
    return {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.FilterWheel.SwitchFilter, NINA.Sequencer",
            "Filter": make_filter(name, FILTER_POSITIONS[name]),
            "Parent": {"$ref": parent}, "ErrorBehavior": 1, "Attempts": 2}

def autofocus(parent):
    return {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Autofocus.RunAutofocus, NINA.Sequencer",
            "Parent": {"$ref": parent}, "ErrorBehavior": 0, "Attempts": 1}

def container(name, parent, items_fn):
    cid = nid(); items = items_fn(cid)
    return {"$id": cid, "$type": "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
            "Strategy": {"$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"},
            "Name": name, "Conditions": coll("cond", []), "IsExpanded": False,
            "Items": coll("item", items), "Triggers": coll("trig", []),
            "Parent": {"$ref": parent}, "ErrorBehavior": 0, "Attempts": 1}

def panel(tile, targets_id, exp, filt):
    dso = nid(); co = radec_parts(tile["ra"], tile["dec"])
    name = sex_name(tile["ra"], tile["dec"], filt)

    def prep(pid):
        return [
            {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Telescope.SlewScopeToRaDec, NINA.Sequencer",
             "Inherited": False,
             "Coordinates": {"$id": nid(), "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry", **co},
             "Parent": {"$ref": pid}, "ErrorBehavior": 1, "Attempts": 2},
            {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Platesolving.Center, NINA.Sequencer",
             "Inherited": False,
             "Coordinates": {"$id": nid(), "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry", **co},
             "Parent": {"$ref": pid}, "ErrorBehavior": 1, "Attempts": 3}]

    def img(iid):
        # no SwitchFilter in here - the block already selected the filter
        return [{"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer",
                 "ExposureTime": exp, "Gain": -1, "Offset": -1,
                 "Binning": {"$id": nid(), "$type": "NINA.Core.Model.Equipment.BinningMode, NINA.Core",
                             "X": 1, "Y": 1},
                 "ImageType": "LIGHT", "ExposureCount": 0,
                 "Parent": {"$ref": iid}, "ErrorBehavior": 0, "Attempts": 1}]

    return {"$id": dso, "$type": "NINA.Sequencer.Container.DeepSkyObjectContainer, NINA.Sequencer",
            "Target": {"$id": nid(), "$type": "NINA.Astrometry.InputTarget, NINA.Astrometry",
                       "Expanded": True, "TargetName": name, "PositionAngle": 0.0,
                       "InputCoordinates": {"$id": nid(), "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry", **co}},
            "ExposureInfoListExpanded": False,
            "ExposureInfoList": {"$id": nid(),
                "$type": "NINA.Core.Utility.AsyncObservableCollection`1[[NINA.Sequencer.Utility.ExposureInfo, NINA.Sequencer]], NINA.Core",
                "$values": [{"$id": nid(), "$type": "NINA.Sequencer.Utility.ExposureInfo, NINA.Sequencer",
                             "Count": 1, "Filter": filt, "ExposureTime": exp, "Gain": 101, "Offset": 30,
                             "ImageType": "LIGHT", "BinningX": 1, "BinningY": 1, "ROI": 1.0}]},
            "Strategy": {"$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"},
            "Name": name,
            "Conditions": coll("cond", []),      # deliberately empty - see module docstring
            "IsExpanded": False,
            "Items": coll("item", [container("Target preparation instructions", dso, prep),
                                   container("Target imaging instructions", dso, img)]),
            "Triggers": coll("trig", []),
            "Parent": {"$ref": targets_id}, "ErrorBehavior": 0, "Attempts": 1}

def af_trigger(type_name, parent, extra):
    tid, run = nid(), nid()
    return {"$id": tid, "$type": type_name, **extra, "Parent": {"$ref": parent},
            "TriggerRunner": {"$id": run, "$type": "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
                "Strategy": {"$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"},
                "Name": None, "Conditions": coll("cond", []), "IsExpanded": True,
                "Items": coll("item", [autofocus(run)]), "Triggers": coll("trig", []),
                "Parent": None, "ErrorBehavior": 0, "Attempts": 1}}

def renumber(doc):
    """Reassign every $id sequentially and remap $ref to match.

    Makes the builder immune to whatever ids the template already uses — the
    generator in index.html does the same thing for the same reason."""
    mapping, counter = {}, [1]

    def assign(o):
        if isinstance(o, list):
            for x in o:
                assign(x)
        elif isinstance(o, dict):
            if "$id" in o:
                mapping[o["$id"]] = str(counter[0]); o["$id"] = str(counter[0]); counter[0] += 1
            for k, v in o.items():
                if k != "$id":
                    assign(v)

    def remap(o):
        if isinstance(o, list):
            for x in o:
                remap(x)
        elif isinstance(o, dict):
            if "$ref" in o and o["$ref"] in mapping:
                o["$ref"] = mapping[o["$ref"]]
            for v in o.values():
                remap(v)

    assign(doc); remap(doc)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--template", required=True, help="your working NINA sequence (Start/End copied verbatim)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--end", default="01:00", help="local cutoff time")
    a = ap.parse_args()

    plan = json.loads(Path(a.plan).read_text())
    doc = json.loads(Path(a.template).read_text())
    by_id = {t["id"]: t for t in build_grid(plan.get("telescope", "visnjan"))}
    tiles = [by_id[i] for i in plan["planned"] if i in by_id]
    exp = float(plan.get("params", {}).get("expSec", EXP_TIME))

    targets = next(i for i in doc["Items"]["$values"] if i.get("Name") == "Targets")
    tid = targets["$id"]
    filters = list(FILTER_POSITIONS)
    items = [wait_until_safe(tid)]
    for i in range(0, len(tiles), AF_BLOCK):
        chunk = tiles[i:i + AF_BLOCK]
        for k, f in enumerate(filters):
            items.append(switch_filter(tid, f))
            items.append(autofocus(tid))
            # serpentine: odd filters retrace the block so the slew home is short
            for t in (reversed(chunk) if k % 2 else chunk):
                items.append(panel(t, tid, exp, f))
    targets["Items"]["$values"] = items
    hh, mm = a.end.split(":")
    targets["Conditions"]["$values"] = [
        {"$id": nid(), "$type": "NINA.Sequencer.Conditions.TimeCondition, NINA.Sequencer",
         "Hours": int(hh), "Minutes": int(mm), "MinutesOffset": 0, "Seconds": 0,
         "SelectedProvider": {"$id": nid(), "$type": "NINA.Sequencer.Utility.DateTimeProvider.TimeProvider, NINA.Sequencer"},
         "Parent": {"$ref": tid}},
        {"$id": nid(), "$type": "NINA.Sequencer.Conditions.SafetyMonitorCondition, NINA.Sequencer",
         "Parent": {"$ref": tid}}]
    targets["Triggers"]["$values"] = [
        af_trigger("NINA.Sequencer.Trigger.Autofocus.AutofocusAfterTemperatureChangeTrigger, NINA.Sequencer", tid, {"Amount": 2.0}),
        af_trigger("NINA.Sequencer.Trigger.Autofocus.AutofocusAfterHFRIncreaseTrigger, NINA.Sequencer", tid, {"Amount": 10.0, "SampleSize": 10})]

    doc["Name"] = "SkyLedger_" + plan["date"] + "_" + plan.get("telescope", "visnjan")
    renumber(doc)
    out = Path(a.out or f"sequences/SkyLedger_{plan['date']}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))

    ids, refs = [], []
    def walk(o):
        if isinstance(o, dict):
            if "$id" in o: ids.append(o["$id"])
            if "$ref" in o and len(o) == 1: refs.append(o["$ref"])
            for k, v in o.items():
                if k != "$id": walk(v)
        elif isinstance(o, list):
            for x in o: walk(x)
    walk(doc)
    assert len(ids) == len(set(ids)), "duplicate $id"
    assert not [r for r in refs if r not in set(ids)], "dangling $ref"
    blocks = -(-len(tiles) // AF_BLOCK) * len(filters)
    print(f"{out}  —  {len(tiles)} tiles, {len(tiles)*len(filters)} panels in {blocks} "
          f"filter blocks, {len(ids)} unique ids, 0 dangling refs")

if __name__ == "__main__":
    main()
