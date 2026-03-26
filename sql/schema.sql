-- =====================================================
-- STEG Data Lakehouse - Complete Schema
-- PostgreSQL 16 on Neon
-- =====================================================

-- Create schemas
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- =====================================================
-- BRONZE LAYER (Raw ingestion tables)
-- =====================================================

-- Production data (monthly)
CREATE TABLE IF NOT EXISTS bronze.production (
    id BIGSERIAL PRIMARY KEY,
    month INT,
    year INT,
    date_raw TEXT,
    steg_production_gwh DECIMAL(12,2),
    ipp_production_gwh DECIMAL(12,2),
    ipp_solar_gwh DECIMAL(12,2),
    autoproducer_gwh DECIMAL(12,2),
    total_production_gwh DECIMAL(12,2),
    source_file TEXT,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- PROSOL adoption data (per region)
CREATE TABLE IF NOT EXISTS bronze.prosol (
    id BIGSERIAL PRIMARY KEY,
    region TEXT,
    year INT,
    surface_m2 DECIMAL(12,2),
    installations_count DECIMAL(12,2),
    subsidy_dt DECIMAL(15,2),
    self_financing_dt DECIMAL(15,2),
    total_investment_dt DECIMAL(15,2),
    avg_price_dt DECIMAL(10,2),
    source_file TEXT,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- STEG districts/agencies
CREATE TABLE IF NOT EXISTS bronze.districts (
    id BIGSERIAL PRIMARY KEY,
    region TEXT,
    gouvernorat TEXT,
    type TEXT,
    district TEXT,
    adresse TEXT,
    standard TEXT,
    reclamation TEXT,
    fax TEXT,
    latitude DECIMAL(10,6),
    longitude DECIMAL(10,6),
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Extracted incident data from PDF
CREATE TABLE IF NOT EXISTS bronze.incidents (
    id BIGSERIAL PRIMARY KEY,
    incident_id TEXT,
    incident_date DATE,
    triggering_event TEXT,
    root_cause TEXT,
    duration_minutes INT,
    power_loss_mw DECIMAL(10,2),
    recovery_time_minutes INT,
    recommendations JSONB,
    key_lessons JSONB,
    technical_failures JSONB,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- International benchmarks (from APIs)
CREATE TABLE IF NOT EXISTS bronze.benchmarks (
    id BIGSERIAL PRIMARY KEY,
    country_code TEXT,
    country_name TEXT,
    indicator_code TEXT,
    indicator_name TEXT,
    year INT,
    value DECIMAL(15,2),
    unit TEXT,
    source TEXT,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Historical weather data (from Open-Meteo)
CREATE TABLE IF NOT EXISTS bronze.weather (
    id BIGSERIAL PRIMARY KEY,
    date DATE,
    latitude DECIMAL(8,4),
    longitude DECIMAL(8,4),
    temperature_2m_mean DECIMAL(5,2),
    precipitation_sum DECIMAL(8,2),
    wind_speed_10m_mean DECIMAL(6,2),
    source TEXT,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- SILVER LAYER (Cleaned, typed, normalized)
-- =====================================================

-- Cleaned production data
CREATE TABLE IF NOT EXISTS silver.production (
    id BIGSERIAL PRIMARY KEY,
    date_key DATE,
    year INT,
    month INT,
    quarter INT,
    month_name TEXT,
    season TEXT,
    steg_production_gwh DECIMAL(12,2),
    ipp_production_gwh DECIMAL(12,2),
    ipp_solar_gwh DECIMAL(12,2),
    autoproducer_gwh DECIMAL(12,2),
    total_production_gwh DECIMAL(12,2),
    steg_share_pct DECIMAL(5,2),
    renewable_share_pct DECIMAL(5,2),
    growth_rate_pct DECIMAL(8,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Cleaned PROSOL data with derived metrics
CREATE TABLE IF NOT EXISTS silver.prosol (
    id BIGSERIAL PRIMARY KEY,
    region TEXT,
    year INT,
    surface_m2 INT,
    installations_count INT,
    subsidy_dt DECIMAL(15,2),
    self_financing_dt DECIMAL(15,2),
    total_investment_dt DECIMAL(15,2),
    avg_price_per_m2 DECIMAL(10,2),
    subsidy_percentage DECIMAL(5,2),
    co2_saved_tons DECIMAL(15,2),
    cumulative_installations INT,
    cumulative_surface INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Cleaned districts with region hierarchy
CREATE TABLE IF NOT EXISTS silver.districts (
    id BIGSERIAL PRIMARY KEY,
    region TEXT,
    gouvernorat TEXT,
    type TEXT,
    district TEXT,
    adresse TEXT,
    standard TEXT,
    fax TEXT,
    latitude DECIMAL(10,6),
    longitude DECIMAL(10,6),
    climate_zone TEXT,
    grid_density TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- GOLD LAYER (KPIs, Aggregated)
-- =====================================================

-- Monthly aggregated production KPIs
CREATE TABLE IF NOT EXISTS gold.monthly_production (
    id BIGSERIAL PRIMARY KEY,
    year INT,
    month INT,
    month_name TEXT,
    total_production_gwh DECIMAL(15,2),
    avg_steg_share_pct DECIMAL(5,2),
    avg_renewable_share_pct DECIMAL(5,2),
    growth_rate_pct DECIMAL(8,2),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seasonal aggregates
CREATE TABLE IF NOT EXISTS gold.seasonal_production (
    id BIGSERIAL PRIMARY KEY,
    year INT,
    season TEXT,
    total_production_gwh DECIMAL(15,2),
    avg_production_gwh DECIMAL(12,2),
    peak_month TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Solar impact metrics per region
CREATE TABLE IF NOT EXISTS gold.solar_impact (
    id BIGSERIAL PRIMARY KEY,
    region TEXT,
    year INT,
    total_surface_m2 INT,
    total_installations INT,
    total_investment_dt DECIMAL(18,2),
    co2_saved_tons DECIMAL(15,2),
    avg_subsidy_pct DECIMAL(5,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Grid stability KPIs
CREATE TABLE IF NOT EXISTS gold.grid_stability (
    id BIGSERIAL PRIMARY KEY,
    year INT,
    quarter INT,
    incident_count INT,
    total_duration_minutes INT,
    avg_severity_score DECIMAL(4,2),
    power_loss_mw DECIMAL(12,2),
    recovery_time_avg_minutes INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Regional consumption aggregates
CREATE TABLE IF NOT EXISTS gold.regional_consumption (
    id BIGSERIAL PRIMARY KEY,
    region TEXT,
    gouvernorat TEXT,
    year INT,
    estimated_consumption_gwh DECIMAL(15,2),
    share_of_national_pct DECIMAL(5,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- International benchmark comparisons
CREATE TABLE IF NOT EXISTS gold.international_comparison (
    id BIGSERIAL PRIMARY KEY,
    year INT,
    country_code TEXT,
    country_name TEXT,
    consumption_per_capita_kwh DECIMAL(12,2),
    renewable_share_pct DECIMAL(5,2),
    solar_adoption_pct DECIMAL(5,2),
    grid_losses_pct DECIMAL(5,2),
    saidi_hours DECIMAL(8,2),
    rank_position INT,
    gap_to_mediterranean_avg_pct DECIMAL(8,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_bronze_production_date ON bronze.production(year, month);
CREATE INDEX IF NOT EXISTS idx_bronze_prosol_region ON bronze.prosol(region);
CREATE INDEX IF NOT EXISTS idx_silver_production_date ON silver.production(date_key);
CREATE INDEX IF NOT EXISTS idx_silver_prosol_region ON silver.prosol(region);
CREATE INDEX IF NOT EXISTS idx_gold_monthly_year ON gold.monthly_production(year);

-- Create views for BI
CREATE OR REPLACE VIEW gold.v_solar_trend AS
SELECT 
    year,
    region,
    total_surface_m2,
    total_installations,
    co2_saved_tons,
    LAG(total_installations) OVER (PARTITION BY region ORDER BY year) AS prev_year_installations,
    (total_installations - LAG(total_installations) OVER (PARTITION BY region ORDER BY year)) * 100.0 / 
        NULLIF(LAG(total_installations) OVER (PARTITION BY region ORDER BY year), 0) AS growth_rate_pct
FROM gold.solar_impact
ORDER BY region, year;

CREATE OR REPLACE VIEW gold.v_production_summary AS
SELECT 
    year,
    month_name,
    total_production_gwh,
    avg_renewable_share_pct,
    growth_rate_pct
FROM gold.monthly_production
ORDER BY year, month;