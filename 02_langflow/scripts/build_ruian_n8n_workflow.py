"""Build n8n/ruian-import.workflow.json: the RUIAN import as an n8n workflow (built-in nodes only).

The schema SQL (DDL, SEARCH_DDL) comes from scripts/ruian_schema.py.

Workflow:
  Start -> Check database (admin) -> If missing: Create database
        -> Prepare schema (DDL, SEARCH_DDL, staging table)
        -> Read zip -> Decompress -> Split Out (one item per CSV) -> Limit (RUIAN_MAX_FILES)
        -> Write CSV to /import/ruian -> COPY into staging (Postgres reads the file, WIN1250)
        -> Load tables (one transaction: TRUNCATE, INSERT ... SELECT DISTINCT, drop staging, ANALYZE)

Run it with the n8n-import service in docker-compose.yml (see README). Usage: uv run python scripts/build_ruian_n8n_workflow.py
"""

import json
from pathlib import Path

from ruian_schema import DDL, SEARCH_DDL

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "n8n" / "ruian-import.workflow.json"

WORKFLOW_ID = "ruianImportN8n"
CRED_ADMIN = {"postgres": {"id": "ruianPgAdmin", "name": "RUIAN Postgres (admin)"}}
CRED_TARGET = {"postgres": {"id": "ruianPgTarget", "name": "RUIAN Postgres (target)"}}
IMPORT_DIR = "/import/ruian"

# All 19 CSV columns as text; types are converted when the tables are loaded.
STAGE_COLUMNS = [
    "kod_adm", "obec_kod", "obec_nazev", "momc_kod", "momc_nazev", "obvod_prahy_kod", "obvod_prahy_nazev",
    "cast_obce_kod", "cast_obce_nazev", "ulice_kod", "ulice_nazev", "typ_so", "cislo_domovni",
    "cislo_orientacni", "znak_cisla_orientacniho", "psc", "souradnice_y", "souradnice_x", "plati_od",
]
STAGE_DDL = (
    "DROP TABLE IF EXISTS ruian_stage;\n"
    "CREATE UNLOGGED TABLE ruian_stage (" + ", ".join(f"{c} text" for c in STAGE_COLUMNS) + ");\n"
)


def t(col: str) -> str:
    """Trimmed text, NULL when empty."""
    return f"nullif(btrim({col}), '')"


# One transaction: if anything fails, the previous data stays. Parents first, so the deferred
# foreign keys are satisfied anyway. DISTINCT ON keeps one row per code.
LOAD_SQL = f"""
BEGIN;
TRUNCATE adresni_misto, ulice, cast_obce, momc, obvod_prahy, obec;
INSERT INTO obec (kod, nazev)
    SELECT DISTINCT ON (obec_kod::int) obec_kod::int, btrim(obec_nazev)
    FROM ruian_stage ORDER BY obec_kod::int;
INSERT INTO obvod_prahy (kod, nazev, obec_kod)
    SELECT DISTINCT ON (obvod_prahy_kod::int) obvod_prahy_kod::int, btrim(obvod_prahy_nazev), obec_kod::int
    FROM ruian_stage WHERE {t('obvod_prahy_kod')} IS NOT NULL ORDER BY obvod_prahy_kod::int;
INSERT INTO momc (kod, nazev, obec_kod, obvod_prahy_kod)
    SELECT DISTINCT ON (momc_kod::int) momc_kod::int, btrim(momc_nazev), obec_kod::int, {t('obvod_prahy_kod')}::int
    FROM ruian_stage WHERE {t('momc_kod')} IS NOT NULL ORDER BY momc_kod::int;
INSERT INTO cast_obce (kod, nazev, obec_kod)
    SELECT DISTINCT ON (cast_obce_kod::int) cast_obce_kod::int, btrim(cast_obce_nazev), obec_kod::int
    FROM ruian_stage ORDER BY cast_obce_kod::int;
INSERT INTO ulice (kod, nazev, obec_kod)
    SELECT DISTINCT ON (ulice_kod::int) ulice_kod::int, btrim(ulice_nazev), obec_kod::int
    FROM ruian_stage WHERE {t('ulice_kod')} IS NOT NULL ORDER BY ulice_kod::int;
-- The full-address and position indexes are rebuilt after the insert; maintaining them row by row is
-- much slower. The position column (poloha) is generated from the coordinates.
DROP INDEX IF EXISTS adresni_misto_adresa_tsv_idx;
DROP INDEX IF EXISTS adresni_misto_poloha_idx;
INSERT INTO adresni_misto (kod, obec_kod, momc_kod, obvod_prahy_kod, cast_obce_kod, ulice_kod, typ_so,
                           cislo_domovni, cislo_orientacni, znak_cisla_orientacniho, psc,
                           souradnice_y, souradnice_x, plati_od, adresa_tsv)
    SELECT kod_adm::int, obec_kod::int, {t('momc_kod')}::int, {t('obvod_prahy_kod')}::int, cast_obce_kod::int,
           {t('ulice_kod')}::int, btrim(typ_so), cislo_domovni::int, {t('cislo_orientacni')}::int,
           {t('znak_cisla_orientacniho')}, btrim(psc), {t('souradnice_y')}::numeric, {t('souradnice_x')}::numeric,
           {t('plati_od')}::timestamp::date,
           ruian_address_words({t('ulice_nazev')}, {t('cast_obce_nazev')}, {t('obec_nazev')}, {t('momc_nazev')},
                               {t('obvod_prahy_nazev')}, cislo_domovni::int, {t('cislo_orientacni')}::int,
                               {t('znak_cisla_orientacniho')}, btrim(psc))
    FROM ruian_stage;
-- Run the deferred foreign-key checks now (all parents are loaded): CREATE INDEX refuses a table with
-- pending trigger events.
SET CONSTRAINTS ALL IMMEDIATE;
CREATE INDEX adresni_misto_adresa_tsv_idx ON adresni_misto USING gin (adresa_tsv);
CREATE INDEX adresni_misto_poloha_idx ON adresni_misto USING spgist (poloha);
DROP TABLE ruian_stage;
COMMIT;
ANALYZE;
SELECT (SELECT count(*) FROM obec) AS obec, (SELECT count(*) FROM obvod_prahy) AS obvod_prahy,
       (SELECT count(*) FROM momc) AS momc, (SELECT count(*) FROM cast_obce) AS cast_obce,
       (SELECT count(*) FROM ulice) AS ulice, (SELECT count(*) FROM adresni_misto) AS adresni_misto;
"""

# Expressions. Values from the environment and the zip are checked before they reach SQL text.
# n8n swallows errors thrown inside expressions (the result becomes "undefined"), so an invalid value
# becomes '' instead: CREATE DATABASE "" and COPY ... FROM '' then fail. n8n/run.sh also checks RUIAN_DB.
DB_NAME = "{{ /^[a-z_][a-z0-9_]{0,62}$/.test($env.RUIAN_DB) ? $env.RUIAN_DB : '' }}"
BINARY_KEY = "Object.keys($binary)[0]"
CSV_PATH = "{{ /^\\/import\\/ruian\\/\\d{8}_OB_\\d+_ADR\\.csv$/.test($json.fileName) ? $json.fileName : '' }}"


def node(name: str, type_: str, version: float, x: int, params: dict, **extra) -> dict:
    return {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": f"n8n-nodes-base.{type_}",
        "typeVersion": version,
        "position": [x, 0],
        "parameters": params,
        **extra,
    }


def pg(name: str, x: int, query: str, creds: dict, **extra) -> dict:
    return node(name, "postgres", 2.6, x, {"operation": "executeQuery", "query": query, "options": {}},
                credentials=creds, **extra)


def main() -> None:
    nodes = [
        node("Start", "manualTrigger", 1, 0, {}),
        pg("Check database", 220,
           f"=SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}') AS db_exists", CRED_ADMIN),
        node("Database exists?", "if", 2.2, 440, {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [{
                    "id": "db-exists",
                    "leftValue": "={{ $json.db_exists }}",
                    "rightValue": "",
                    "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                }],
                "combinator": "and",
            },
            "options": {},
        }),
        pg("Create database", 660, f'=CREATE DATABASE "{DB_NAME}"', CRED_ADMIN),
        pg("Prepare schema", 880, DDL + SEARCH_DDL + STAGE_DDL, CRED_TARGET, executeOnce=True),
        node("Read zip", "readWriteFile", 1.1, 1100, {
            "operation": "read",
            "fileSelector": "/data/*_OB_ADR_csv.zip",
            "options": {},
        }, executeOnce=True),
        node("Decompress", "compression", 1.1, 1320, {
            "operation": "decompress", "binaryPropertyName": "data", "outputPrefix": "file_",
        }),
        node("One item per CSV", "splitOut", 1, 1540, {"fieldToSplitOut": "$binary", "options": {}}),
        node("Max files", "limit", 1, 1760, {
            "maxItems": "={{ Number($env.RUIAN_MAX_FILES) > 0 ? Number($env.RUIAN_MAX_FILES) : 1000000 }}",
        }),
        node("Write CSV", "readWriteFile", 1.1, 1980, {
            "operation": "write",
            "fileName": f"={IMPORT_DIR}/{{{{ $binary[{BINARY_KEY}].fileName.split('/').pop() }}}}",
            "dataPropertyName": f"={{{{ {BINARY_KEY} }}}}",
            "options": {},
        }),
        pg("COPY into staging", 2200,
           f"=COPY ruian_stage FROM '{CSV_PATH}' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'WIN1250')",
           CRED_TARGET),
        pg("Load tables", 2420, LOAD_SQL, CRED_TARGET, executeOnce=True),
    ]

    def to(target: str) -> list:
        return [{"node": target, "type": "main", "index": 0}]

    connections = {
        "Start": {"main": [to("Check database")]},
        "Check database": {"main": [to("Database exists?")]},
        "Database exists?": {"main": [to("Prepare schema"), to("Create database")]},  # [true, false]
        "Create database": {"main": [to("Prepare schema")]},
        "Prepare schema": {"main": [to("Read zip")]},
        "Read zip": {"main": [to("Decompress")]},
        "Decompress": {"main": [to("One item per CSV")]},
        "One item per CSV": {"main": [to("Max files")]},
        "Max files": {"main": [to("Write CSV")]},
        "Write CSV": {"main": [to("COPY into staging")]},
        "COPY into staging": {"main": [to("Load tables")]},
    }

    workflow = {
        "id": WORKFLOW_ID,
        "name": "RUIAN import",
        "active": False,
        "settings": {"executionOrder": "v1", "saveManualExecutions": False},
        "nodes": nodes,
        "connections": connections,
        "pinData": {},
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
