"""Source registry catalog: the eight data groups of the spec (§3.1, §18.1) plus the geography authority.

A connector that cannot run without an authorization or a manual file is registered with that status — never as
`connected`. Release classes: LIVE_EXTERNAL, RELEASED_PERIODIC, SLOW_MOVING, PLANNER_INPUT, SYNTHETIC_FIXTURE.
"""
from __future__ import annotations

from typing import Any

from ..core import db
from ..core.util import dumps, iso, utcnow

DISEASES: dict[str, dict[str, Any]] = {
    "dengue": {
        "label": "Dengue", "signal_type": "notification",
        "metric_label": "Reported dengue cases", "units": "reported cases per week",
        "definition": "Dengue notifications per municipality and epidemiological week as published by InfoDengue (SINAN-based). "
                      "Counts may be revised after publication and do not represent all infections.",
        "targets": ["dengue_reported_cases", "dengue_estimated_cases"],
    },
    "chikungunya": {
        "label": "Chikungunya", "signal_type": "notification",
        "metric_label": "Reported chikungunya cases", "units": "reported cases per week",
        "definition": "Chikungunya notifications per municipality-week from InfoDengue. Separate signal from dengue.",
        "targets": ["chikungunya_reported_cases"],
    },
    "covid19_sari": {
        "label": "COVID-19 SARI", "signal_type": "sari_admission",
        "metric_label": "Reported severe respiratory cases attributed to COVID-19", "units": "SARI cases per week",
        "definition": "Severe acute respiratory illness (SARI) cases classified as COVID-19 in SIVEP-Gripe, aggregated to "
                      "municipality of residence and epidemiological week of symptom onset. Not all COVID-19 cases.",
        "targets": ["covid19_sari_admissions"],
    },
    "influenza_sari": {
        "label": "Influenza SARI", "signal_type": "sari_admission",
        "metric_label": "Reported severe respiratory cases attributed to influenza", "units": "SARI cases per week",
        "definition": "SARI cases classified as influenza in SIVEP-Gripe. Not community influenza incidence.",
        "targets": ["influenza_sari_admissions"],
    },
}

TARGETS: dict[str, dict[str, str]] = {
    "dengue_reported_cases": {"disease_id": "dengue", "column": "observed_cases", "signal_type": "notification",
                              "label": "Reported dengue cases"},
    "dengue_estimated_cases": {"disease_id": "dengue", "column": "estimated_cases", "signal_type": "estimated_count",
                               "label": "Nowcast-estimated dengue cases (InfoDengue)"},
    "chikungunya_reported_cases": {"disease_id": "chikungunya", "column": "observed_cases", "signal_type": "notification",
                                   "label": "Reported chikungunya cases"},
    "covid19_sari_admissions": {"disease_id": "covid19_sari", "column": "observed_cases", "signal_type": "sari_admission",
                                "label": "COVID-19 SARI cases"},
    "influenza_sari_admissions": {"disease_id": "influenza_sari", "column": "observed_cases", "signal_type": "sari_admission",
                                  "label": "Influenza SARI cases"},
}

SOURCES: list[dict[str, Any]] = [
    {"source_id": "ibge_municipalities", "display_name": "IBGE municipality register", "source_group": "geography",
     "connector_type": "http_csv", "status": "available", "pii_class": "public",
     "license_ref": "IBGE codes and centroids via kelvins/municipios-brasileiros (MIT)",
     "provenance_url": "https://github.com/kelvins/municipios-brasileiros", "expected_grain": "municipality",
     "expected_cadence": "slow-moving", "release_class": "SLOW_MOVING", "freshness_days": 3650, "publication_lag_days": 0,
     "approved_hostnames": ["raw.githubusercontent.com"],
     "config": {"url": "https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv"}},
    {"source_id": "ibge_geometry", "display_name": "IBGE municipal boundaries", "source_group": "geography",
     "connector_type": "georaster", "status": "available", "pii_class": "public",
     "license_ref": "IBGE malha municipal (public), mirrored by tbrugz/geodata-br (MIT); verify-at-source",
     "provenance_url": "https://github.com/tbrugz/geodata-br", "expected_grain": "municipality polygon",
     "expected_cadence": "slow-moving", "release_class": "SLOW_MOVING", "freshness_days": 3650, "publication_lag_days": 0,
     "approved_hostnames": ["raw.githubusercontent.com"],
     "config": {"url": "https://raw.githubusercontent.com/tbrugz/geodata-br/master/geojson/geojs-100-mun.json",
                "simplify_tolerance_deg": 0.004}},
    {"source_id": "infodengue", "display_name": "InfoDengue", "source_group": "disease",
     "connector_type": "http_json", "status": "available", "pii_class": "aggregated",
     "license_ref": "InfoDengue (Fiocruz/FGV) open API; cite Codeço et al.; verify-at-source",
     "provenance_url": "https://info.dengue.mat.br/", "expected_grain": "municipality-week + release vintage",
     "expected_cadence": "weekly", "release_class": "RELEASED_PERIODIC", "freshness_days": 14, "publication_lag_days": 3,
     "approved_hostnames": ["info.dengue.mat.br"],
     "config": {"base_url": "https://info.dengue.mat.br/api/alertcity", "diseases": ["dengue", "chikungunya"],
                "start_year": 2018, "rate_limit_s": 0.4}},
    {"source_id": "open_meteo", "display_name": "Open-Meteo historical weather", "source_group": "climate",
     "connector_type": "http_json", "status": "available", "pii_class": "public",
     "license_ref": "Open-Meteo CC BY 4.0 (ERA5 reanalysis, Copernicus)", "provenance_url": "https://open-meteo.com/en/docs",
     "expected_grain": "coordinate-day (reanalysis)", "expected_cadence": "daily", "release_class": "LIVE_EXTERNAL",
     "freshness_days": 14, "publication_lag_days": 6, "approved_hostnames": ["archive-api.open-meteo.com"],
     "config": {"base_url": "https://archive-api.open-meteo.com/v1/archive", "start_date": "2017-12-01",
                "batch_size": 10, "min_weekly_coverage": 0.85}},
    {"source_id": "nager_holidays", "display_name": "Nager.Date public holidays", "source_group": "calendar",
     "connector_type": "http_json", "status": "available", "pii_class": "public", "license_ref": "Nager.Date MIT",
     "provenance_url": "https://date.nager.at/", "expected_grain": "country-date", "expected_cadence": "yearly",
     "release_class": "SLOW_MOVING", "freshness_days": 365, "publication_lag_days": 0, "approved_hostnames": ["date.nager.at"],
     "config": {"base_url": "https://date.nager.at/api/v3/PublicHolidays", "country": "BR", "start_year": 2017, "years_ahead": 1}},
    {"source_id": "cnes_facilities_beds", "display_name": "CNES facilities and registered beds", "source_group": "healthcare",
     "connector_type": "http_json", "status": "available", "pii_class": "public",
     "license_ref": "Ministério da Saúde dados abertos (CNES); verify-at-source",
     "provenance_url": "https://apidadosabertos.saude.gov.br/", "expected_grain": "establishment / hospital",
     "expected_cadence": "monthly", "release_class": "RELEASED_PERIODIC", "freshness_days": 60, "publication_lag_days": 30,
     "approved_hostnames": ["apidadosabertos.saude.gov.br", "dadosabertos.saude.gov.br", "s3.sa-east-1.amazonaws.com"],
     "config": {"facilities_url": "https://apidadosabertos.saude.gov.br/cnes/estabelecimentos",
                "beds_dataset_page": "https://dadosabertos.saude.gov.br/dataset/hospitais-e-leitos",
                "page_size": 20, "max_pages_per_municipality": 400, "rate_limit_s": 0.15}},
    {"source_id": "sivep_gripe", "display_name": "SIVEP-Gripe SARI (OpenDataSUS SRAG)", "source_group": "disease",
     "connector_type": "http_bulk", "status": "available", "pii_class": "restricted_personal",
     "license_ref": "OpenDataSUS SRAG open data; row-level file kept read-only in Bronze, only counts reach Silver",
     "provenance_url": "https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026",
     "expected_grain": "case row -> municipality-week", "expected_cadence": "weekly", "release_class": "RELEASED_PERIODIC",
     "freshness_days": 14, "publication_lag_days": 7,
     "approved_hostnames": ["dadosabertos.saude.gov.br", "s3.sa-east-1.amazonaws.com"],
     "config": {"dataset_page": "https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026", "years_back": 2,
                "delimiter": ";", "small_cell_suppression": 0, "max_file_bytes": 900000000}},
    {"source_id": "brazil_dengue_multimodal", "display_name": "Brazil Dengue Multimodal (Zenodo)", "source_group": "climate",
     "connector_type": "http_bulk", "status": "available", "pii_class": "aggregated",
     "license_ref": "CC BY 4.0 — Zenodo record 22029053 (DATASET_MULTIMODAL_V8); cite the record DOI",
     "provenance_url": "https://zenodo.org/records/22029053",
     "expected_grain": "municipality-week", "expected_cadence": "frozen", "release_class": "SLOW_MOVING",
     "freshness_days": None, "publication_lag_days": 28, "approved_hostnames": ["zenodo.org"],
     "config": {"record_api": "https://zenodo.org/api/records/22029053", "file": "dataset_multimodal_v8.parquet",
                "max_file_bytes": 900000000, "release_lag_days": 21}},
    {"source_id": "gadm_boundaries", "display_name": "GADM 4.1 administrative polygons", "source_group": "geography",
     "connector_type": "http_geojson", "status": "available", "pii_class": "public",
     "license_ref": "GADM 4.1: free for academic and non-commercial use; redistribution needs permission (gadm.org/license) — "
                    "used here only to key Meta mobility polygons, the product map uses IBGE and OCHA polygons",
     "provenance_url": "https://gadm.org/", "expected_grain": "admin polygon", "expected_cadence": "slow-moving",
     "release_class": "SLOW_MOVING", "freshness_days": 3650, "publication_lag_days": 0,
     "approved_hostnames": ["geodata.ucdavis.edu"],
     "config": {"url_templates": {"4.1": "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{code}.json.zip",
                                  "3.6": "https://geodata.ucdavis.edu/gadm/gadm3.6/json/gadm36_{code}.json.zip"},
                # Meta Movement Distribution keys Viet Nam on GADM 4.1 ids but Brazil on an older version (3.6): the ids are NOT
                # interchangeable across versions (checked in healthpulse/geo/pipeline.py), so both versions are kept.
                "layers": {"4.1": ["BRA_2", "VNM_1", "VNM_2"], "3.6": ["BRA_2"]}}},
    {"source_id": "meta_mobility", "display_name": "Meta Movement Distribution (distance from home)", "source_group": "mobility",
     "connector_type": "http_bulk", "status": "available", "pii_class": "aggregated",
     "license_ref": "Meta Data for Good, Movement Distribution Maps via HDX, CC BY 4.0. Keyed by GADM ids, joined through the "
                    "polygon crosswalk (healthpulse/geo). A distance-from-home indicator, not origin-destination flows.",
     "provenance_url": "https://data.humdata.org/dataset/movement-distribution",
     "expected_grain": "GADM polygon-day, 4 distance-from-home bands", "expected_cadence": "biweekly",
     "release_class": "RELEASED_PERIODIC", "freshness_days": 30, "publication_lag_days": 2,
     "approved_hostnames": ["data.humdata.org", "s3.us-east-1.amazonaws.com"],
     "config": {"package_api": "https://data.humdata.org/api/3/action/package_show?id=movement-distribution",
                "countries": ["BRA", "VNM"], "files": 2, "workers": 4, "max_file_bytes": 600000000}},
    {"source_id": "ibge_commuting", "display_name": "IBGE commuting integration (Census 2010 arranjos)", "source_group": "mobility",
     "connector_type": "http_xlsx", "status": "available", "pii_class": "aggregated",
     "license_ref": "IBGE Arranjos Populacionais e Concentrações Urbanas, 2nd ed. (Census 2010 commuting sample), public",
     "provenance_url": "https://www.ibge.gov.br/geociencias/organizacao-do-territorio/divisao-regional/15782-arranjos-populacionais-e-concentracoes-urbanas-do-brasil.html",
     "expected_grain": "municipality (arranjo membership + integration index)", "expected_cadence": "census",
     "release_class": "SLOW_MOVING", "freshness_days": None, "publication_lag_days": 0, "approved_hostnames": ["geoftp.ibge.gov.br"],
     "config": {"url": "https://geoftp.ibge.gov.br/organizacao_do_territorio/divisao_regional/arranjos_populacionais/tabelas_xls_2ed/tab01.xlsx"}},
    {"source_id": "worldpop", "display_name": "WorldPop population grid (1 km)", "source_group": "population",
     "connector_type": "georaster", "status": "available", "pii_class": "public",
     "license_ref": "WorldPop CC BY 4.0 (Global 2015–2030 R2025A, constrained, 1 km)", "provenance_url": "https://hub.worldpop.org/",
     "expected_grain": "raster/year -> municipality", "expected_cadence": "yearly", "release_class": "SLOW_MOVING",
     "freshness_days": None, "publication_lag_days": 0, "approved_hostnames": ["data.worldpop.org"],
     "config": {"url_template": "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/{year}/BRA/v1/1km_ua/constrained/bra_pop_{year}_CN_1km_R2025A_UA_v1.tif",
                "release": "R2025A", "max_year": 2030}},
    {"source_id": "ibge_population", "display_name": "IBGE population estimates (SIDRA 6579)", "source_group": "population",
     "connector_type": "http_json", "status": "available", "pii_class": "public",
     "license_ref": "IBGE SIDRA public API", "provenance_url": "https://sidra.ibge.gov.br/tabela/6579",
     "expected_grain": "municipality-year", "expected_cadence": "yearly", "release_class": "SLOW_MOVING",
     "freshness_days": 400, "publication_lag_days": 240, "approved_hostnames": ["apisidra.ibge.gov.br"],
     "config": {"base_url": "https://apisidra.ibge.gov.br/values", "start_year": 2015}},
    {"source_id": "geoboundaries", "display_name": "geoBoundaries ADM2", "source_group": "geography",
     "connector_type": "georaster", "status": "available", "pii_class": "public",
     "license_ref": "CC BY 3.0 IGO", "provenance_url": "https://www.geoboundaries.org/", "expected_grain": "polygon",
     "expected_cadence": "slow-moving", "release_class": "SLOW_MOVING", "freshness_days": None, "publication_lag_days": None,
     "approved_hostnames": ["www.geoboundaries.org"],
     "config": {"meta_url": "https://www.geoboundaries.org/api/current/gbOpen/BRA/ADM2/",
                "note": "Probe only: polygons carry no IBGE code, so name-based crosswalk is refused; IBGE geometry is the authority."}},
    {"source_id": "ibge_cempre", "display_name": "IBGE CEMPRE workforce by sector (SIDRA 6450)", "source_group": "workforce",
     "connector_type": "http_json", "status": "available", "pii_class": "aggregated",
     "license_ref": "IBGE SIDRA public API (CEMPRE, CNAE 2.0 sections)", "provenance_url": "https://sidra.ibge.gov.br/tabela/6450",
     "expected_grain": "municipality-year-sector", "expected_cadence": "yearly", "release_class": "SLOW_MOVING",
     "freshness_days": None, "publication_lag_days": 365, "approved_hostnames": ["apisidra.ibge.gov.br"],
     "config": {"base_url": "https://apisidra.ibge.gov.br/values", "years": 3,
                "columns": ["municipality_id", "year", "cnae_section", "formal_workers"]}},
    {"source_id": "synthetic_fixture", "display_name": "Synthetic fixture generator", "source_group": "fixture",
     "connector_type": "synthetic", "status": "available", "pii_class": "synthetic",
     "license_ref": "Generated locally — SYNTHETIC, never a data product", "provenance_url": None,
     "expected_grain": "any", "expected_cadence": "on demand", "release_class": "SYNTHETIC_FIXTURE",
     "freshness_days": None, "publication_lag_days": None, "approved_hostnames": [],
     "config": {"kinds": ["disease_weekly", "sivep_rows"]}},
]


# code-owned fields refreshed on start for registry rows an admin has never edited (config_version == 1)
_UPGRADABLE = ("display_name", "connector_type", "source_group", "pii_class", "license_ref", "provenance_url", "expected_grain",
               "expected_cadence", "release_class", "freshness_days", "publication_lag_days")


def seed_sources() -> None:
    now = iso(utcnow())
    for s in SOURCES:
        row = db.q1("SELECT * FROM source_registry WHERE source_id=?", s["source_id"])
        if row:
            if row["config_version"] == 1:
                upd = {k: s[k] for k in _UPGRADABLE}
                upd["config_json"] = dumps(s["config"])
                upd["approved_hostnames"] = dumps(s["approved_hostnames"])
                if row["status"] in ("manual_import_required", "needs_authorization") and s["status"] == "available":
                    upd["status"] = "available"
                if any(str(row[k]) != str(v) for k, v in upd.items()):
                    db.update("source_registry", {"source_id": s["source_id"]}, {**upd, "updated_at": now})
            continue
        db.insert("source_registry", {
            "source_id": s["source_id"], "display_name": s["display_name"], "owner_id": "u_operator",
            "connector_type": s["connector_type"], "source_group": s["source_group"], "status": s["status"],
            "enabled": 1, "config_json": dumps(s["config"]), "config_version": 1, "auth_secret_ref": None,
            "pii_class": s["pii_class"], "license_ref": s["license_ref"], "provenance_url": s["provenance_url"],
            "expected_grain": s["expected_grain"], "expected_cadence": s["expected_cadence"],
            "release_class": s["release_class"], "approved_hostnames": dumps(s["approved_hostnames"]),
            "freshness_days": s["freshness_days"], "publication_lag_days": s["publication_lag_days"],
            "schedule_cron": None, "updated_at": now,
        })


def get_source(source_id: str) -> dict | None:
    return db.jcol(db.q1("SELECT * FROM source_registry WHERE source_id=?", source_id), "config_json", "approved_hostnames")
