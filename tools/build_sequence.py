"""Build a N.I.N.A. Advanced Sequencer file from a saved night plan.

    python tools/build_sequence.py data/nights/night_2026-09-12.json \
           --template templates/observatory_base.json

Takes your own working sequence as the template: its Start and End areas are
copied through byte-for-byte, and only the "Targets" container is replaced.

Structure per block: Switch Filter -> Run Autofocus -> N panels, alternating
G and R, each block re-imaging the same tiles in the other filter. Panels carry
NO conditions: in NINA a container with conditions REPEATS while they hold, so a
per-panel condition makes each panel loop forever instead of advancing.
"""
import argparse, json
from pathlib import Path
from grid import build_grid

CHUNK = 30
EXP_TIME = 30.0
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

_first = {}
def filter_value(name):
    if name not in _first:
        o = make_filter(name, FILTER_POSITIONS[name]); _first[name] = o["$id"]; return o
    return {"$ref": _first[name]}

def switch_filter(parent, name):
    return {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.FilterWheel.SwitchFilter, NINA.Sequencer",
            "Filter": filter_value(name), "Parent": {"$ref": parent}, "ErrorBehavior": 0, "Attempts": 1}

def autofocus(parent):
    return {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Autofocus.RunAutofocus, NINA.Sequencer",
            "Parent": {"$ref": parent}, "ErrorBehavior": 0, "Attempts": 1}

def container(name, parent, items_fn):
    cid = nid(); items = items_fn(cid)
    return {"$id": cid, "$type": "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
            "Strategy": {"$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"},
            "Name": name, "Conditions": coll("cond", []), "IsExpanded": True,
            "Items": coll("item", items), "Triggers": coll("trig", []),
            "Parent": {"$ref": parent}, "ErrorBehavior": 0, "Attempts": 1}

def panel(tile, filt, targets_id, exp):
    dso = nid(); name = f"{tile['id']}_{filt}"; co = radec_parts(tile["ra"], tile["dec"])

    def prep(pid):
        return [
            {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Telescope.SlewScopeToRaDec, NINA.Sequencer",
             "Inherited": False,
             "Coordinates": {"$id": nid(), "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry", **co},
             "Parent": {"$ref": pid}, "ErrorBehavior": 1, "Attempts": 2},
            {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Platesolving.Center, NINA.Sequencer",
             "Inherited": False,
             "Coordinates": {"$id": nid(), "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry", **co},
             "Parent": {"$ref": pid}, "ErrorBehavior": 0, "Attempts": 3},
            {"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Guider.StartGuiding, NINA.Sequencer",
             "ForceCalibration": False, "Parent": {"$ref": pid}, "ErrorBehavior": 0, "Attempts": 1}]

    def img(iid):
        return [{"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer",
                 "ExposureTime": exp, "Gain": -1, "Offset": -1,
                 "Binning": {"$id": nid(), "$type": "NINA.Core.Model.Equipment.BinningMode, NINA.Core", "X": 1, "Y": 1},
                 "ImageType": "LIGHT", "ExposureCount": 0,
                 "Parent": {"$ref": iid}, "ErrorBehavior": 0, "Attempts": 1}]

    def close(cid):
        return [{"$id": nid(), "$type": "NINA.Sequencer.SequenceItem.Guider.StopGuiding, NINA.Sequencer",
                 "Parent": {"$ref": cid}, "ErrorBehavior": 0, "Attempts": 1}]

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
                                   container("Target imaging instructions", dso, img),
                                   container("Target closure instructions", dso, close)]),
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--template", required=True, help="your working NINA sequence (Start/End copied verbatim)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--chunk", type=int, default=CHUNK)
    ap.add_argument("--end", default="01:00", help="local cutoff time")
    a = ap.parse_args()

    plan = json.loads(Path(a.plan).read_text())
    doc = json.loads(Path(a.template).read_text())
    by_id = {t["id"]: t for t in build_grid()}
    tiles = [by_id[i] for i in plan["planned"] if i in by_id]
    exp = float(plan.get("params", {}).get("expSec", EXP_TIME))

    targets = next(i for i in doc["Items"]["$values"] if i.get("Name") == "Targets")
    tid = targets["$id"]
    keep = [i for i in targets["Items"]["$values"]
            if i.get("$type", "").endswith("WaitUntilSafe, NINA.Sequencer")]

    items, blocks = [], 0
    for s in range(0, len(tiles), a.chunk):
        chunk = tiles[s:s + a.chunk]
        for filt in ("G", "R"):
            items.append(switch_filter(tid, filt))
            items.append(autofocus(tid))
            items += [panel(t, filt, tid, exp) for t in chunk]
            blocks += 1

    targets["Items"]["$values"] = keep + items
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

    doc["Name"] = "SkyLedger_" + plan["date"]
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
    print(f"{out}  —  {len(tiles)} tiles, {len(tiles)*2} panels, {blocks} blocks, "
          f"{len(ids)} unique ids, 0 dangling refs")

if __name__ == "__main__":
    main()
