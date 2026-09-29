#!/usr/bin/env python3
"""Apply the pre-registered hand-reading step: walk the ranking (duplicates removed), skip scripts removed by reading
(listed with reasons in <results>/hand_review.json), and write the first 10 survivors to <results>/final_top10.json.

hand_review.json: {"removed": [{"file": ..., "reason": ...}], "read_ok": [{"file": ..., "note": ...}]}
Usage: python3 experiments/pine/final_top10.py --results results/pine_final
"""
import argparse, json, os
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results/pine_final")
    ap.add_argument("--n", type=int, default=10)
    a = ap.parse_args()
    rank = pd.read_csv(os.path.join(a.results, "ranking.csv"))
    rank["rank"] = range(1, len(rank) + 1)
    hr_path = os.path.join(a.results, "hand_review.json")
    hr = json.load(open(hr_path)) if os.path.exists(hr_path) else {"removed": [], "read_ok": []}
    removed = {r["file"]: r["reason"] for r in hr.get("removed", [])}
    notes = {r["file"]: r.get("note", "") for r in hr.get("read_ok", [])}
    kept = rank[rank.duplicate_of.isna()]
    out, skipped = [], []
    for r in kept.itertuples():
        if r.file in removed:
            skipped.append({"file": r.file, "rank": int(r.rank), "reason": removed[r.file]})
            continue
        row = r._asdict()
        row.pop("Index", None)
        row["hand_note"] = notes.get(r.file, "")
        row["read"] = r.file in notes
        out.append(row)
        if len(out) >= a.n:
            break
    unread = [o["file"] for o in out if not o["read"]]
    json.dump({"top": out, "removed_on_the_way": skipped, "unread": unread},
              open(os.path.join(a.results, "final_top10.json"), "w"), indent=1, default=str)
    for k, o in enumerate(out, 1):
        print(f"{k:2d}. rank {o['rank']:4d} score {o['score']:.3f} {'read' if o['read'] else 'UNREAD'}  {o['file']}")
    for s in skipped:
        print(f"    removed (rank {s['rank']}): {s['file']}: {s['reason']}")
    if unread:
        print(f"{len(unread)} of the top {a.n} still need reading")


if __name__ == "__main__":
    main()
