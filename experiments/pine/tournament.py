#!/usr/bin/env python3
"""Run every Pine script in the vault index on Binance perps (BTCUSDT, ETHUSDT) and store daily equity + trades.

Resumable: results/meta go to <out>/meta.jsonl (one line per script x symbol) and <out>/runs/<key>.npz.
Usage: PYTHONPATH=src python3 experiments/pine/tournament.py --index data/pine/index.parquet --out data/pine/t1 \
          --symbols BTCUSDT,ETHUSDT --max-bars 200000 --workers 4 [--sample 200] [--files a.md,b.md]
"""
import argparse, hashlib, json, os, re, signal, sys, time, traceback
from multiprocessing import Pool
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

END_MS = 1759103999999 + 1          # placeholder, replaced in main from --end
UNIT = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def tf_ms_of(period) -> int:
    if not isinstance(period, str):
        return 3_600_000
    m = re.match(r"^(\d+)([mhdw])$", period.strip())
    return int(m.group(1)) * UNIT[m.group(2)] if m else 3_600_000


def run_key(file: str, symbol: str) -> str:
    return hashlib.sha1(f"{file}|{symbol}".encode()).hexdigest()[:16]


def classify(e: BaseException) -> str:
    from pinebt.parser import PineSyntaxError, Unsupported
    from pinebt.compiler import CompileError
    from pinebt.engine import UnsupportedSymbol
    if isinstance(e, PineSyntaxError):
        return "parse_error"
    if isinstance(e, (Unsupported, UnsupportedSymbol)):
        return "unsupported"
    if isinstance(e, CompileError):
        return "compile_error"
    if isinstance(e, TimeoutError) or type(e).__name__ == "HardTimeout":
        return "timeout"
    return "crash"


def daily(TC, eq):
    tc = np.asarray(TC, np.int64)
    e = np.asarray(eq, float)
    day = (tc - 1) // 86_400_000
    last = np.r_[day[1:] != day[:-1], True]
    d, v = day[last], e[last]
    full = np.arange(d[0], d[-1] + 1)
    idx = np.searchsorted(d, full, side="right") - 1
    return full.astype(np.int32), v[idx]


class HardTimeout(Exception):
    pass


def _alarm(signum, frame):
    raise HardTimeout("hard per-run time limit")


def work(task):
    file, src, tf_ms, symbols, max_bars, end_ms, fee, out, time_limit = task
    from pinebt.engine import CompiledScript, Runner
    signal.signal(signal.SIGALRM, _alarm)
    metas = []
    t0 = time.time()
    try:
        cs = CompiledScript(src)
    except RecursionError as e:
        return [{"file": file, "symbol": s, "status": "compile_error", "error": "recursion"} for s in symbols]
    except Exception as e:
        st = classify(e)
        return [{"file": file, "symbol": s, "status": st, "error": f"{type(e).__name__}: {str(e)[:200]}"} for s in symbols]
    compile_s = time.time() - t0
    for sym in symbols:
        meta = {"file": file, "symbol": sym, "tf_ms": tf_ms, "flags": sorted(cs.flags), "pyramiding": cs.cfg["pyramiding"],
                "poc": cs.cfg["process_orders_on_close"], "decl": cs.cfg["decl"], "compile_s": round(compile_s, 3)}
        try:
            signal.alarm(int(time_limit * 1.5) + 30)
            try:
                r = Runner(cs, sym, tf_ms, end_ms=end_ms, max_bars=max_bars, fee=fee, time_limit=time_limit).run()
            finally:
                signal.alarm(0)
            days, deq = daily(r["TC"], r["equity"])
            cl = r["closed"]
            tr = np.array([[c[3], c[4], c[5], c[6], c[7], c[8]] for c in cl], float) if cl else np.zeros((0, 6))
            np.savez_compressed(os.path.join(out, "runs", run_key(file, sym) + ".npz"), days=days, eq=deq, trades=tr)
            nb = r["n_bars"]
            status = "ok" if r["err_bars"] == 0 else "runtime_error"
            meta.update(status=status, n_bars=nb, first_bar=r["T"][0] if nb else None, seconds=round(r["seconds"], 2),
                        us_per_bar=round(1e6 * r["seconds"] / max(1, nb), 1), err_bars=r["err_bars"],
                        first_err=r["first_err"], blown=r["blown"], n_closed=len(cl), n_fills=r["n_fills"],
                        fees=round(r["fees"], 2), funding=round(r["funding"], 2), eq_end=round(r["equity"][-1], 4) if nb else None,
                        sec_calls=r["sec_calls"])
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                raise
            meta.update(status=classify(e), error=f"{type(e).__name__}: {str(e)[:200]}",
                        tb=traceback.format_exc()[-600:] if classify(e) == "crash" else None)
        metas.append(meta)
    return metas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="data/pine/index.parquet")
    ap.add_argument("--out", default="data/pine/t1")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--max-bars", type=int, default=200_000)
    ap.add_argument("--end", default="2026-09-29")
    ap.add_argument("--fee", type=float, default=0.0007)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--time-limit", type=float, default=900.0)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--files", default="")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "runs"), exist_ok=True)
    end_ms = int(pd.Timestamp(a.end, tz="UTC").timestamp() * 1000)
    df = pd.read_parquet(a.index)
    p = df[df.lang == "pinescript"].copy()
    if a.files:
        p = p[p.file.isin(a.files.split(","))]
    if a.sample:
        p = p.sample(min(a.sample, len(p)), random_state=a.seed)
    done = set()
    meta_path = os.path.join(a.out, "meta.jsonl")
    if os.path.exists(meta_path):
        for line in open(meta_path):
            try:
                m = json.loads(line)
                done.add(m["file"])
            except Exception:
                pass
    syms = a.symbols.split(",")
    p["tf_ms"] = p.bt_period.map(tf_ms_of)
    p = p[~p.file.isin(done)].sort_values(["tf_ms", "file"])
    tasks = [(r.file, r.source, int(r.tf_ms), syms, a.max_bars, end_ms, a.fee, a.out, a.time_limit) for r in p.itertuples()]
    print(f"{len(tasks)} scripts to run ({len(done)} already done)", flush=True)
    t0 = time.time()
    n = 0
    with Pool(a.workers, maxtasksperchild=40) as pool, open(meta_path, "a") as fo:
        for metas in pool.imap_unordered(work, tasks, chunksize=1):
            for m in metas:
                fo.write(json.dumps(m, default=str) + "\n")
            fo.flush()
            n += 1
            if n % 50 == 0:
                el = time.time() - t0
                print(f"{n}/{len(tasks)} scripts, {el/60:.1f} min, eta {el/n*(len(tasks)-n)/60:.0f} min", flush=True)
    print(f"done {n} in {(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
