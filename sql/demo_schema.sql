-- Explicit demo initialization ONLY. Never run against a populated MOSIDI database.
-- Independently authored from publicly documented column definitions at MOSIDI
-- commit 26cadbba3d6223c84872987104fc737359bf8bfc. This is NOT THB's authoritative dump.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE public."Kommunale Gebiete Deutschland" (
    id SERIAL PRIMARY KEY,
    geom geometry(MULTIPOLYGON,4326),
    objid VARCHAR(16), beginn DATE, ade INTEGER, gf INTEGER, bsg INTEGER,
    ars VARCHAR(12), nationalco VARCHAR(8), sdv_ars VARCHAR(12),
    name VARCHAR(60), bez VARCHAR(60), ibz INTEGER, bem VARCHAR(60),
    nbd VARCHAR(4), sn_l VARCHAR(2), sn_r VARCHAR(1), sn_k VARCHAR(2),
    sn_v1 VARCHAR(2), sn_v2 VARCHAR(2), sn_g VARCHAR(3), fk_s3 VARCHAR(2),
    nuts VARCHAR(5), ars_0 VARCHAR(12), ags_0 VARCHAR(8), wsk DATE
);

CREATE TABLE public.dashboard_data_de (
    id SERIAL PRIMARY KEY,
    indikator TEXT, kennziffer TEXT, wert NUMERIC, zeitbezug INTEGER,
    granularity VARCHAR(50), source VARCHAR(200), data_type VARCHAR(50)
);

CREATE TABLE public.table_metadata (
    dct_title TEXT, dct_description TEXT, dct_catalog_title TEXT,
    dct_catalog_description TEXT, dct_catalog_publisher TEXT,
    dct_accessurl TEXT, dct_license TEXT, dct_identifier TEXT,
    dcatde_contributorid TEXT, dct_distribution TEXT, dct_language TEXT,
    dct_bbox geometry, dct_centroid geometry, geometry_type TEXT,
    dcatde_politicalgeocodingleveluri TEXT NOT NULL,
    dcatde_politicalgeocodinguri TEXT, dcatde_geocodingtext TEXT,
    dct_modified TIMESTAMP, dct_issued TIMESTAMP, dct_accrualperiodicity TEXT,
    dct_temporal_startdata TIMESTAMP, dct_temporal_enddate TIMESTAMP,
    table_name TEXT NOT NULL, details JSONB, imported TIMESTAMP,
    dct_type TEXT, legend_url TEXT, dcat_ap_id TEXT, dcat_ap_title TEXT,
    PRIMARY KEY (table_name,dcatde_politicalgeocodingleveluri)
);

CREATE MATERIALIZED VIEW public.source_granularities AS
    SELECT DISTINCT source,indikator,granularity FROM public.dashboard_data_de;
CREATE UNIQUE INDEX source_granularities_unique
    ON public.source_granularities(source,indikator,granularity);
