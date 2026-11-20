"""Schema of the `ruian` database: tables (DDL) and the fulltext search columns (SEARCH_DDL).

Used by scripts/build_ruian_n8n_workflow.py: the n8n import's "Prepare schema" step runs DDL and
SEARCH_DDL on every import. After a change, regenerate the n8n workflow
(``uv run python scripts/build_ruian_n8n_workflow.py``) and run the import again.
"""

# Order matters: parent tables first, so the foreign keys can reference them.
DDL = """
CREATE TABLE IF NOT EXISTS obec (
    kod   integer PRIMARY KEY,
    nazev text NOT NULL
);
CREATE TABLE IF NOT EXISTS obvod_prahy (
    kod      integer PRIMARY KEY,
    nazev    text NOT NULL,
    obec_kod integer NOT NULL REFERENCES obec (kod) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE IF NOT EXISTS momc (
    kod             integer PRIMARY KEY,
    nazev           text NOT NULL,
    obec_kod        integer NOT NULL REFERENCES obec (kod) DEFERRABLE INITIALLY DEFERRED,
    obvod_prahy_kod integer REFERENCES obvod_prahy (kod) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE IF NOT EXISTS cast_obce (
    kod      integer PRIMARY KEY,
    nazev    text NOT NULL,
    obec_kod integer NOT NULL REFERENCES obec (kod) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE IF NOT EXISTS ulice (
    kod      integer PRIMARY KEY,
    nazev    text NOT NULL,
    obec_kod integer NOT NULL REFERENCES obec (kod) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE IF NOT EXISTS adresni_misto (
    kod                    integer PRIMARY KEY,
    obec_kod               integer NOT NULL REFERENCES obec (kod) DEFERRABLE INITIALLY DEFERRED,
    momc_kod               integer REFERENCES momc (kod) DEFERRABLE INITIALLY DEFERRED,
    obvod_prahy_kod        integer REFERENCES obvod_prahy (kod) DEFERRABLE INITIALLY DEFERRED,
    cast_obce_kod          integer NOT NULL REFERENCES cast_obce (kod) DEFERRABLE INITIALLY DEFERRED,
    ulice_kod              integer REFERENCES ulice (kod) DEFERRABLE INITIALLY DEFERRED,
    typ_so                 text NOT NULL CHECK (typ_so IN ('č.p.', 'č.ev.')),
    cislo_domovni          integer NOT NULL,
    cislo_orientacni       integer,
    znak_cisla_orientacniho text,
    psc                    char(5) NOT NULL,
    souradnice_y           numeric(12, 2),
    souradnice_x           numeric(12, 2),
    plati_od               date NOT NULL
);
CREATE INDEX IF NOT EXISTS adresni_misto_obec_idx ON adresni_misto (obec_kod);
CREATE INDEX IF NOT EXISTS adresni_misto_cast_obce_idx ON adresni_misto (cast_obce_kod);
CREATE INDEX IF NOT EXISTS adresni_misto_ulice_idx ON adresni_misto (ulice_kod);
CREATE INDEX IF NOT EXISTS adresni_misto_psc_idx ON adresni_misto (psc);
COMMENT ON TABLE obec IS 'Obec (Kód obce / Název obce)';
COMMENT ON TABLE obvod_prahy IS 'Obvod Prahy (Kód obvodu Prahy), jen Praha';
COMMENT ON TABLE momc IS 'Městský obvod / městská část (Kód MOMC), jen členěná statutární města';
COMMENT ON TABLE cast_obce IS 'Část obce';
COMMENT ON TABLE ulice IS 'Ulice (jen obce se zavedenou uliční sítí)';
COMMENT ON TABLE adresni_misto IS 'Adresní místo (Kód ADM); souřadnice v S-JTSK [m]';
"""

# Fulltext search on names, used by the RUIAN Search component (components/ruian_search_component.py).
# Idempotent: it also upgrades a database that was loaded before these columns existed
# (ALTER TABLE ... ADD COLUMN IF NOT EXISTS).
SEARCH_DDL = """
CREATE EXTENSION IF NOT EXISTS unaccent;
-- lower() + unaccent(), declared IMMUTABLE so it can be used in generated columns and indexes.
CREATE OR REPLACE FUNCTION ruian_norm(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    RETURN public.unaccent('public.unaccent'::regdictionary, lower($1));
ALTER TABLE obec ADD COLUMN IF NOT EXISTS nazev_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, ruian_norm(nazev))) STORED;
ALTER TABLE obvod_prahy ADD COLUMN IF NOT EXISTS nazev_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, ruian_norm(nazev))) STORED;
ALTER TABLE momc ADD COLUMN IF NOT EXISTS nazev_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, ruian_norm(nazev))) STORED;
ALTER TABLE cast_obce ADD COLUMN IF NOT EXISTS nazev_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, ruian_norm(nazev))) STORED;
ALTER TABLE ulice ADD COLUMN IF NOT EXISTS nazev_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, ruian_norm(nazev))) STORED;
CREATE INDEX IF NOT EXISTS obec_nazev_tsv_idx ON obec USING gin (nazev_tsv);
CREATE INDEX IF NOT EXISTS obvod_prahy_nazev_tsv_idx ON obvod_prahy USING gin (nazev_tsv);
CREATE INDEX IF NOT EXISTS momc_nazev_tsv_idx ON momc USING gin (nazev_tsv);
CREATE INDEX IF NOT EXISTS cast_obce_nazev_tsv_idx ON cast_obce USING gin (nazev_tsv);
CREATE INDEX IF NOT EXISTS ulice_nazev_tsv_idx ON ulice USING gin (nazev_tsv);
-- Parent filters of the search tools.
CREATE INDEX IF NOT EXISTS cast_obce_obec_idx ON cast_obce (obec_kod);
CREATE INDEX IF NOT EXISTS ulice_obec_idx ON ulice (obec_kod);
CREATE INDEX IF NOT EXISTS adresni_misto_momc_idx ON adresni_misto (momc_kod);
CREATE INDEX IF NOT EXISTS adresni_misto_obvod_prahy_idx ON adresni_misto (obvod_prahy_kod);
"""
