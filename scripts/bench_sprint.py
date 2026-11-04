"""Replay IMDC validation tests: python scripts/bench_sprint.py [dev|tests|all] [models...]
dev = seasons 2015-2021 (model development); tests = the sprint's validation tests 1-4 (2022-2025)."""
import sys, time
import polars as pl
from healthpulse.sprint import data as D, models as M, score as S

which = sys.argv[1] if len(sys.argv) > 1 else "tests"
models = sys.argv[2:] or list(M.MODELS)
seasons = {"dev": range(2015, 2022), "tests": D.TESTS.values(), "all": range(2015, 2026)}[which]
d = D.uf_weekly()
f = M.SeasonForecaster(d)
t = time.time()
sc, pk = {}, {}
for m in dict.fromkeys(["climatology", *models]):
    fcs = [x for s in seasons for x in f.run(m, s)]
    sc[m], pk[m] = S.score(fcs, d), S.peak_errors(fcs, d)
pl.Config.set_tbl_rows(100); pl.Config.set_tbl_cols(20)
summ = S.summary(sc)
print(summ)
print(summ.group_by("model").agg(pl.col("rel_wis").log().mean().exp().alias("rel_wis_gm"), pl.col("mean_wis").mean(),
      pl.col("cov50").mean(), pl.col("cov90").mean(), pl.col("cov95").mean()).sort("rel_wis_gm"))
for m, p in pk.items():
    if len(p):
        print(m, p.select(pl.col("peak_week_err").abs().median().alias("peak_wk_abs_med"),
                          pl.col("total_log_ratio").abs().median().alias("tot_abs_logratio_med")).row(0))
print(f"{time.time()-t:.1f}s")
