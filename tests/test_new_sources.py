"""Offline tests for the connectors that close the documented data gaps (no network)."""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import numpy as np
import polars as pl

from healthpulse.core.xlsx import read_rows
from healthpulse.sources.connectors import _read_leitos
from healthpulse.sources.connectors_extra import _sidra_value, aggregate_srag, zonal_sums


def test_zonal_sums_exact_cell_centre_rule():
    # 10x10 grid of 1-degree cells, origin (0, 10), every cell = 1 person
    r = np.ones((10, 10), dtype=np.float32)
    sq = {"type": "Feature", "properties": {"id": "1234567"},
          "geometry": {"type": "Polygon", "coordinates": [[[2, 8], [5, 8], [5, 4], [2, 4], [2, 8]]]}}
    # polygon with a hole: outer 0..10 x 0..10, hole 4..6 x 4..6
    holed = {"type": "Feature", "properties": {"id": "7654321"},
             "geometry": {"type": "Polygon", "coordinates": [[[0, 10], [10, 10], [10, 0], [0, 0], [0, 10]],
                                                             [[4, 6], [6, 6], [6, 4], [4, 4], [4, 6]]]}}
    out = {o["municipality_id"]: o for o in zonal_sums(r, 0.0, 10.0, 1.0, [sq, holed])}
    assert out["1234567"]["worldpop_population"] == 12  # 3 columns x 4 rows of centres inside
    assert out["7654321"]["worldpop_population"] == 96  # 100 - 4 hole cells
    # nodata (negative) cells never count
    r2 = r.copy()
    r2[2, 2] = -99999
    assert zonal_sums(r2, 0.0, 10.0, 1.0, [sq])[0]["worldpop_population"] == 11


def test_aggregate_srag_parquet_counts_only(tmp_path):
    p = tmp_path / "INFLUD26-test.parquet"
    pl.DataFrame({
        "DT_SIN_PRI": [datetime(2026, 3, 2), datetime(2026, 3, 3), datetime(2026, 3, 9), None],
        "CO_MUN_RES": ["330455", "330455", "355030", "330455"],
        "CLASSI_FIN": [5, 5, 1, 5],
        "NM_PACIENTE": ["a", "b", "c", "d"],  # personal column present in the file, never read
    }).write_parquet(p)
    agg = aggregate_srag([p])
    assert set(agg.columns) == {"m6", "week_start", "disease_id", "observed_cases"}
    rio = agg.filter((pl.col("m6") == "330455") & (pl.col("disease_id") == "covid19_sari"))
    assert rio["observed_cases"].to_list() == [2]
    assert rio["week_start"].to_list() == [date(2026, 3, 1)]  # epi week starts on Sunday
    assert agg.filter(pl.col("disease_id") == "influenza_sari")["observed_cases"].sum() == 1


def test_leitos_csv_zip_and_codes():
    csv = ('"COMP","UF","CNES","NOME_ESTABELECIMENTO",LEITOS_EXISTENTES,LEITOS_SUS,UTI_TOTAL_EXIST,UTI_TOTAL_SUS\n'
           '"202601","RJ","27","HOSPITAL A",64,10,4,2\n').encode("latin-1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Leitos_2026.csv", csv)
    for body in (csv, buf.getvalue()):
        df = _read_leitos("beds_x", body)
        assert df["CNES"].to_list() == ["0000027"]
        assert df["LEITOS_EXISTENTES"].to_list() == ["64"]


def test_sidra_symbols():
    assert _sidra_value("-") == 0
    assert _sidra_value("X") is None and _sidra_value("..") is None and _sidra_value(None) is None
    assert _sidra_value("6731133") == 6731133


def test_xlsx_reader_minimal():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                                           '<si><t>Rio de Janeiro (RJ)</t></si><si><t>Sim</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                                               '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>3304557</v></c>'
                                               '<c r="D1" t="s"><v>1</v></c><c r="E1"><v>0.5</v></c></row></sheetData></worksheet>')
    assert read_rows(buf.getvalue()) == [["Rio de Janeiro (RJ)", 3304557, None, "Sim", 0.5]]


def test_sari_timeseries_without_nowcast_columns(client):
    """SIVEP SARI rows have no InfoDengue nowcast columns; the region series must return nulls, not a 500."""
    from datetime import datetime, timezone
    from healthpulse.sources import fixtures
    weeks = pl.date_range(date(2026, 1, 4), date(2026, 3, 1), interval="1w", eager=True)
    df = pl.DataFrame({"week_start": weeks, "disease_id": "covid19_sari", "observed_cases": list(range(len(weeks))),
                       "municipality_id": "3304557", "signal_type": "sari_admission"}).with_columns(
        pl.lit(datetime(2026, 3, 10, tzinfo=timezone.utc)).alias("release_approx_at"))
    fixtures.publish("sari_weekly", df, datetime(2026, 3, 10, tzinfo=timezone.utc))
    r = client.get("/api/v1/regions/3304557/timeseries?disease_id=covid19_sari&weeks=52")
    assert r.status_code == 200, r.text
    obs = r.json()["data"]["observed"]
    assert obs and obs[0]["estimated_cases"] is None and obs[-1]["observed_cases"] == len(weeks) - 1
