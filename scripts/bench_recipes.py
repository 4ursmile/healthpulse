"""Fast recipe benchmark: replays a training dataset's rolling-origin folds and prints CV + test MAE per horizon.
Usage: python scripts/bench_recipes.py [dataset_id] [recipe,recipe,...]"""
import json, sqlite3, sys
from datetime import date, timedelta
import numpy as np, polars as pl

sys.path.insert(0, ".")
from healthpulse.ml import recipes as R

DS = sys.argv[1] if len(sys.argv) > 1 else "ds_01m6exwp273b63z2j7gz290g9y"
DT = pl.Datetime("us", "UTC")
c = sqlite3.connect("data/healthpulse.db")
split = json.loads(c.execute("select splits_json from training_dataset where dataset_id=?", (DS,)).fetchone()[0])
df = pl.read_parquet(f"data/gold/training_sets/{DS}/train.parquet").filter(pl.col("label").is_not_null())
meta = {"municipality_id", "issue_week", "issue_at", "horizon", "target_week", "anchor_week", "max_input_available_at",
        "pit_approx", "label", "label_known_at", "label_approx", "label_release_approx_at"}
feats = [x for x in df.columns if x not in meta and not x.endswith("__known_at")]
cut = lambda d: pl.lit(date.fromisoformat(d)).cast(DT) + pl.duration(days=1)
folds = split["folds"] + [dict(split["test"], fold="test", validation_start=split["test"]["start"], validation_end=split["test"]["end"])]
cands = sys.argv[2].split(",") if len(sys.argv) > 2 else list(R.RECIPES)
QS = R.QUANTILES if hasattr(R, "QUANTILES") else None

out = {}
for rid in cands:
    for h in (1, 2, 3, 4):
        hf = df.filter(pl.col("horizon") == h)
        for fold in folds:
            tr = hf.filter(pl.col("label_release_approx_at") <= cut(fold["train_label_cutoff"]))
            va = hf.filter((pl.col("issue_week") >= date.fromisoformat(fold["validation_start"])) & (pl.col("issue_week") <= date.fromisoformat(fold["validation_end"])))
            m = R.fit(rid, tr, feats, h, None, 42)
            y = va["label"].cast(pl.Float64).to_numpy()
            p = m.predict(va)
            k = "test" if fold["fold"] == "test" else "cv"
            out.setdefault((rid, h, k), []).append((y, p))
print(f"{'recipe':22s} " + " ".join(f"cv_h{h:<6d}" for h in (1, 2, 3, 4)) + " | " + " ".join(f"te_h{h:<6d}" for h in (1, 2, 3, 4)) + " | cvMean teMean")
for rid in cands:
    row = []
    for k in ("cv", "test"):
        for h in (1, 2, 3, 4):
            y = np.concatenate([a for a, _ in out[(rid, h, k)]]); p = np.concatenate([b for _, b in out[(rid, h, k)]])
            row.append(np.mean(np.abs(p - y)))
    print(f"{rid:22s} " + " ".join(f"{v:9.1f}" for v in row[:4]) + " | " + " ".join(f"{v:9.1f}" for v in row[4:]) + f" | {np.mean(row[:4]):6.1f} {np.mean(row[4:]):6.1f}")
