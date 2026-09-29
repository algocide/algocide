#!/usr/bin/env python3
"""Merge a re-run (same scripts, newer engine) into the main tournament directory and check what changed.

Every re-run (file, symbol) replaces its first-pass meta line and npz. Runs whose code did not change and whose
security data stayed under the bar cap must reproduce the first pass exactly; anything else is reported as unexpected.
Usage: python3 experiments/pine/merge_rerun.py --main data/pine/t1 --rerun data/pine/t1_rerun \
          --changed-code data/pine/rerun/changed_code.json
"""
import argparse, json, os, shutil, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tournament import run_key            # noqa: E402


def load_meta(path):
    out = []
    for line in open(path):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def same_run(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return (np.array_equal(a["days"], b["days"]) and np.array_equal(a["eq"], b["eq"])
            and np.array_equal(a["trades"], b["trades"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default="data/pine/t1")
    ap.add_argument("--rerun", default="data/pine/t1_rerun")
    ap.add_argument("--changed-code", default="data/pine/rerun/changed_code.json")
    a = ap.parse_args()
    changed_code = set(json.load(open(a.changed_code)))
    main_meta_path = os.path.join(a.main, "meta.jsonl")
    backup = os.path.join(a.main, "meta_first_pass.jsonl")
    if not os.path.exists(backup):
        shutil.copyfile(main_meta_path, backup)
    first = load_meta(backup)
    re_meta = load_meta(os.path.join(a.rerun, "meta.jsonl"))
    re_files = {m["file"] for m in re_meta}
    first_by_key = {(m["file"], m["symbol"]): m for m in first}
    old_runs = os.path.join(a.main, "runs_first_pass")
    os.makedirs(old_runs, exist_ok=True)

    rows = []
    for m in re_meta:
        key = run_key(m["file"], m["symbol"])
        old_p = os.path.join(old_runs, key + ".npz")
        if not os.path.exists(old_p) and os.path.exists(os.path.join(a.main, "runs", key + ".npz")):
            shutil.copyfile(os.path.join(a.main, "runs", key + ".npz"), old_p)
        new_p = os.path.join(a.rerun, "runs", key + ".npz")
        old = dict(np.load(old_p)) if os.path.exists(old_p) else None
        new = dict(np.load(new_p)) if os.path.exists(new_p) else None
        prev = first_by_key.get((m["file"], m["symbol"]), {})
        capped = any(c for _, c in (m.get("shadow_bars") or {}).values())
        same = same_run(old, new) and prev.get("status") == m.get("status")
        reason = "identical" if same else ("code_changed" if m["file"] in changed_code
                                           else "security_capped" if capped else "UNEXPECTED")
        rows.append({"file": m["file"], "symbol": m["symbol"], "old_status": prev.get("status"),
                     "new_status": m.get("status"), "shadow_capped": capped, "result": reason,
                     "old_eq_end": prev.get("eq_end"), "new_eq_end": m.get("eq_end")})
        if new is not None:
            shutil.copyfile(new_p, os.path.join(a.main, "runs", key + ".npz"))
        elif os.path.exists(os.path.join(a.main, "runs", key + ".npz")):
            os.remove(os.path.join(a.main, "runs", key + ".npz"))

    merged = [m for m in first if m["file"] not in re_files] + re_meta
    with open(main_meta_path + ".tmp", "w") as fo:
        for m in merged:
            fo.write(json.dumps(m, default=str) + "\n")
    os.replace(main_meta_path + ".tmp", main_meta_path)

    counts = {}
    for r in rows:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    report = {"n_rerun_runs": len(rows), "counts": counts, "rows": rows}
    json.dump(report, open(os.path.join(a.main, "rerun_report.json"), "w"), indent=1, default=str)
    print(json.dumps(counts))
    for r in rows:
        if r["result"] == "UNEXPECTED":
            print("UNEXPECTED", r)


if __name__ == "__main__":
    main()
