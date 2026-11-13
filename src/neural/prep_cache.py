"""Build a training store's table cache and feature schema once, before parallel training runs share them
(samplers.cached_table_dir and train.feature_schema write without a lock, so two seeds starting together on a new store
could both write them).

    python -m src.neural.prep_cache --store STORE --workers 31
"""
from __future__ import annotations

import argparse
import resource
import sys
import time
from pathlib import Path

from src.neural import samplers as S, train as T


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    tcfg = T.training_config(T.load_config())
    info = T.store_info(args.store)
    t = T.load_train_table(args.store)
    print(f"table {len(t.a['run']):,} specimens, splits {t.names['splits']}, {time.perf_counter() - t0:.0f} s; "
          f"store_hash {info['store_hash'][:16]}", flush=True)
    T.feature_schema(t, info, tcfg, workers=args.workers, table_cache=S.CACHE_ROOT)
    print(f"schema and table cache {S.cached_table_dir(t)} in {time.perf_counter() - t0:.0f} s; "
          f"peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20:.1f} GB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
