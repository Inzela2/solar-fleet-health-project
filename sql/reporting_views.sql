-- Reporting layer for the Power BI dashboard.
-- Power BI reads these views, never the raw tables, so the dashboard has a stable star-schema
-- contract even if the pipeline tables change. Idempotent: safe to run on every pipeline start.

CREATE SCHEMA IF NOT EXISTS reporting;

-- Dimension: one row per installation, with the metadata flags the pipeline raises.
CREATE OR REPLACE VIEW reporting.dim_site AS
SELECT
    s.site_id,
    s.capacity_kw::float8                                    AS capacity_kw,
    s.postcode,
    CASE
        WHEN s.capacity_kw < 1.5 THEN '1. Under 1.5 kW'
        WHEN s.capacity_kw < 3   THEN '2. 1.5 to 3 kW'
        ELSE                          '3. 3 kW and over'
    END                                                      AS capacity_band,
    COALESCE(a.total_alerts, 0)                              AS total_alerts,
    a.last_alert_day
FROM sites s
LEFT JOIN (
    SELECT site_id, count(*) AS total_alerts, max(day) AS last_alert_day
    FROM alerts GROUP BY site_id
) a USING (site_id);

-- Dimension: a contiguous calendar covering every day the pipeline has metrics for.
-- Power BI time intelligence (rolling 28 days, month-to-date) needs a gap-free date table.
CREATE OR REPLACE VIEW reporting.dim_date AS
SELECT
    d::date                                  AS date,
    extract(year  FROM d)::int               AS year,
    extract(month FROM d)::int               AS month_no,
    to_char(d, 'Mon YYYY')                   AS month_label,
    date_trunc('month', d)::date             AS month_start,
    extract(isodow FROM d)::int              AS weekday_no,
    to_char(d, 'Dy')                         AS weekday,
    CASE WHEN extract(month FROM d) IN (12, 1, 2) THEN 'Summer'
         WHEN extract(month FROM d) IN (3, 4, 5)  THEN 'Autumn'
         WHEN extract(month FROM d) IN (6, 7, 8)  THEN 'Winter'
         ELSE 'Spring' END                   AS season            -- southern hemisphere
FROM generate_series(
        (SELECT min(day) FROM daily_site_metrics),
        (SELECT max(day) FROM daily_site_metrics),
        interval '1 day') AS d;

-- Fact: one row per site per day. Flags are computed here so DAX measures stay simple sums.
CREATE OR REPLACE VIEW reporting.fact_daily AS
SELECT
    m.site_id,
    m.day                                                    AS date,
    m.gen_kwh::float8                                        AS gen_kwh,
    m.cons_kwh::float8                                       AS cons_kwh,
    m.gen_intervals,
    m.yield_kwh_per_kw::float8                               AS yield_kwh_per_kw,
    m.fleet_yield::float8                                    AS fleet_yield,
    m.perf_ratio::float8                                     AS perf_ratio,
    (m.gen_intervals >= 46)::int                             AS is_complete_day,
    (m.gen_kwh > 0)::int                                     AS is_generating_day,
    (EXISTS (SELECT 1 FROM alerts x
             WHERE x.site_id = m.site_id AND x.day = m.day
               AND x.alert_type = 'no_generation'))::int     AS is_outage_day
FROM daily_site_metrics m;

-- Fact: one row per alert. alert_type is no_generation, underperforming or missing_data.
CREATE OR REPLACE VIEW reporting.fact_alerts AS
SELECT
    site_id,
    day                                                      AS date,
    alert_type,
    severity,
    CASE severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END AS severity_order,
    detail,
    created_at
FROM alerts;

-- Pipeline health page: each ingest batch with its outcome.
CREATE OR REPLACE VIEW reporting.pipeline_runs AS
SELECT
    run_id, started_at, finished_at, date_from, date_to,
    rows_in, rows_loaded, rows_quarantined, status,
    extract(epoch FROM finished_at - started_at)::int        AS duration_seconds,
    rows_quarantined::float8 / NULLIF(rows_in, 0)            AS quarantine_rate
FROM ingest_runs;

-- Least-privilege login for Power BI: it can read the reporting views and nothing else.
-- Change the password before using this anywhere but a local demo.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'powerbi_reader') THEN
        CREATE ROLE powerbi_reader LOGIN PASSWORD 'powerbi_reader';
    END IF;
END $$;
GRANT USAGE  ON SCHEMA reporting TO powerbi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO powerbi_reader;

-- Row-level security mapping for the Power BI "Postcode Manager" role: which postcodes each
-- login (Power BI user principal name) may see. Empty by default.
CREATE TABLE IF NOT EXISTS reporting.site_access (
    user_email  text    NOT NULL,
    postcode    integer NOT NULL,
    PRIMARY KEY (user_email, postcode)
);
GRANT SELECT ON reporting.site_access TO powerbi_reader;
