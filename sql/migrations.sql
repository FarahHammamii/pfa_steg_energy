-- =====================================================
-- CORRECTED MIGRATION: STEG Dashboard
-- Preserves existing silver.cut_history structure
-- =====================================================

-- Ensure schemas exist
CREATE SCHEMA IF NOT EXISTS public;
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- =====================================================
-- 1. WORKERS TABLE (Directly linked to districts)
-- =====================================================
CREATE TABLE IF NOT EXISTS public.workers (
    id              BIGSERIAL PRIMARY KEY,
    worker_id       TEXT UNIQUE NOT NULL,
    full_name       TEXT NOT NULL,
    email           TEXT UNIQUE,
    phone           TEXT,
    district        TEXT NOT NULL,
    gouvernorat     TEXT,
    role            TEXT NOT NULL DEFAULT 'worker',
    password_hash   TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login      TIMESTAMP,
    CONSTRAINT valid_role CHECK (role IN ('worker', 'supervisor', 'admin'))
);

CREATE INDEX IF NOT EXISTS idx_workers_worker_id ON public.workers(worker_id);
CREATE INDEX IF NOT EXISTS idx_workers_district ON public.workers(district);
CREATE INDEX IF NOT EXISTS idx_workers_role ON public.workers(role);
CREATE INDEX IF NOT EXISTS idx_workers_email ON public.workers(email);

-- =====================================================
-- 2. CUT PLANS
-- =====================================================
CREATE TABLE IF NOT EXISTS public.cut_plans (
    id               BIGSERIAL PRIMARY KEY,
    plan_ref         TEXT UNIQUE NOT NULL,
    proposed_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    proposed_by      TEXT NOT NULL DEFAULT 'agent',
    proposed_by_worker_id BIGINT REFERENCES public.workers(id) ON DELETE SET NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    approved_by      TEXT,
    approved_by_worker_id BIGINT REFERENCES public.workers(id) ON DELETE SET NULL,
    approved_at      TIMESTAMP,
    rejected_by      TEXT,
    rejected_by_worker_id BIGINT REFERENCES public.workers(id) ON DELETE SET NULL,
    rejected_at      TIMESTAMP,
    rejection_reason TEXT,
    scheduled_date   DATE,
    scheduled_start  TIME,
    estimated_duration_hours DECIMAL(4,1),
    deficit_pct      DECIMAL(6,2),
    alert_level      TEXT NOT NULL DEFAULT 'GREEN',
    regions          TEXT[] NOT NULL DEFAULT '{}',
    protected_regions TEXT[] NOT NULL DEFAULT '{}',
    cut_advisor_output  JSONB,
    fairness_output     JSONB,
    forecast_output     JSONB,
    ai_explanation      TEXT,
    notified_citizens   BOOLEAN NOT NULL DEFAULT FALSE,
    notification_sent_at TIMESTAMP,
    created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT valid_status CHECK (status IN ('pending', 'approved', 'rejected', 'executed')),
    CONSTRAINT valid_alert CHECK (alert_level IN ('GREEN', 'YELLOW', 'ORANGE', 'RED'))
);

CREATE INDEX IF NOT EXISTS idx_cut_plans_status ON public.cut_plans(status);
CREATE INDEX IF NOT EXISTS idx_cut_plans_date ON public.cut_plans(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_cut_plans_alert ON public.cut_plans(alert_level);
CREATE INDEX IF NOT EXISTS idx_cut_plans_created ON public.cut_plans(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cut_plans_regions ON public.cut_plans USING GIN(regions);

-- =====================================================
-- 3. CUT EXECUTIONS
-- =====================================================
CREATE TABLE IF NOT EXISTS public.cut_executions (
    id                 BIGSERIAL PRIMARY KEY,
    cut_plan_id        BIGINT NOT NULL REFERENCES public.cut_plans(id) ON DELETE CASCADE,
    worker_id          TEXT NOT NULL,
    worker_db_id       BIGINT REFERENCES public.workers(id) ON DELETE SET NULL,
    worker_name        TEXT,
    district           TEXT NOT NULL,
    region             TEXT NOT NULL,
    gouvernorat        TEXT,
    status             TEXT NOT NULL DEFAULT 'pending',
    actual_start       TIMESTAMP,
    actual_end         TIMESTAMP,
    actual_duration_minutes INT,
    planned_start      TIMESTAMP,
    planned_end        TIMESTAMP,
    variance_minutes   INT GENERATED ALWAYS AS (
                           CASE WHEN actual_start IS NOT NULL AND planned_start IS NOT NULL
                                THEN EXTRACT(EPOCH FROM (actual_start - planned_start))::INT / 60
                                ELSE NULL END
                       ) STORED,
    notes              TEXT,
    obstacles          TEXT,
    created_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT valid_execution_status CHECK (status IN ('pending', 'in_progress', 'restored', 'incident'))
);

CREATE INDEX IF NOT EXISTS idx_cut_exec_plan ON public.cut_executions(cut_plan_id);
CREATE INDEX IF NOT EXISTS idx_cut_exec_worker ON public.cut_executions(worker_id);
CREATE INDEX IF NOT EXISTS idx_cut_exec_region ON public.cut_executions(region);
CREATE INDEX IF NOT EXISTS idx_cut_exec_status ON public.cut_executions(status);
CREATE INDEX IF NOT EXISTS idx_cut_exec_worker_db ON public.cut_executions(worker_db_id);
CREATE INDEX IF NOT EXISTS idx_cut_exec_actual_start ON public.cut_executions(actual_start);

-- =====================================================
-- 4. NOTIFICATION LOG
-- =====================================================
CREATE TABLE IF NOT EXISTS public.notification_log (
    id              BIGSERIAL PRIMARY KEY,
    cut_plan_id     BIGINT REFERENCES public.cut_plans(id) ON DELETE SET NULL,
    region          TEXT NOT NULL,
    gouvernorat     TEXT,
    channel         TEXT NOT NULL DEFAULT 'sms',
    recipient_count INT  NOT NULL DEFAULT 0,
    message_ar      TEXT,
    message_fr      TEXT,
    status          TEXT NOT NULL DEFAULT 'sent',
    provider        TEXT,
    sent_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    error_detail    TEXT,
    CONSTRAINT valid_channel CHECK (channel IN ('sms', 'email', 'push', 'webhook'))
);

CREATE INDEX IF NOT EXISTS idx_notif_plan ON public.notification_log(cut_plan_id);
CREATE INDEX IF NOT EXISTS idx_notif_region ON public.notification_log(region);
CREATE INDEX IF NOT EXISTS idx_notif_sent_at ON public.notification_log(sent_at DESC);

-- =====================================================
-- 5. CITIZEN PREFERENCES
-- =====================================================
CREATE TABLE IF NOT EXISTS public.citizen_preferences (
    id              BIGSERIAL PRIMARY KEY,
    phone           TEXT,
    email           TEXT,
    region          TEXT NOT NULL,
    gouvernorat     TEXT,
    lang            TEXT NOT NULL DEFAULT 'ar',
    notify_sms      BOOLEAN NOT NULL DEFAULT TRUE,
    notify_email    BOOLEAN NOT NULL DEFAULT FALSE,
    verified        BOOLEAN NOT NULL DEFAULT FALSE,
    verification_token TEXT,
    unsubscribe_token TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT citizen_contact CHECK (phone IS NOT NULL OR email IS NOT NULL),
    CONSTRAINT valid_lang CHECK (lang IN ('ar', 'fr', 'en'))
);

CREATE INDEX IF NOT EXISTS idx_citizen_region ON public.citizen_preferences(region);
CREATE INDEX IF NOT EXISTS idx_citizen_phone ON public.citizen_preferences(phone);
CREATE INDEX IF NOT EXISTS idx_citizen_email ON public.citizen_preferences(email);
CREATE INDEX IF NOT EXISTS idx_citizen_verified ON public.citizen_preferences(verified);

-- =====================================================
-- 6. CITIZEN INCIDENT REPORTS
-- =====================================================
CREATE TABLE IF NOT EXISTS public.citizen_reports (
    id              BIGSERIAL PRIMARY KEY,
    region          TEXT NOT NULL,
    gouvernorat     TEXT,
    report_type     TEXT NOT NULL,
    description     TEXT,
    reported_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    phone           TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    resolved_at     TIMESTAMP,
    resolution_note TEXT,
    CONSTRAINT valid_report_status CHECK (status IN ('open', 'in_progress', 'resolved', 'closed'))
);

CREATE INDEX IF NOT EXISTS idx_citizen_reports_region ON public.citizen_reports(region);
CREATE INDEX IF NOT EXISTS idx_citizen_reports_status ON public.citizen_reports(status);
CREATE INDEX IF NOT EXISTS idx_citizen_reports_date ON public.citizen_reports(reported_at DESC);

-- =====================================================
-- 7. AGENT RUN LOG
-- =====================================================
CREATE TABLE IF NOT EXISTS public.agent_run_log (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT UNIQUE NOT NULL,
    triggered_by    TEXT NOT NULL DEFAULT 'cron',
    triggered_by_worker_id BIGINT REFERENCES public.workers(id) ON DELETE SET NULL,
    started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMP,
    alert_level     TEXT,
    cut_advisor_status  TEXT,
    fairness_status     TEXT,
    forecast_status     TEXT,
    cut_plan_id     BIGINT REFERENCES public.cut_plans(id) ON DELETE SET NULL,
    error           TEXT,
    full_output     JSONB,
    duration_seconds DECIMAL(8,2) GENERATED ALWAYS AS (
        CASE WHEN finished_at IS NOT NULL AND started_at IS NOT NULL
             THEN EXTRACT(EPOCH FROM (finished_at - started_at))
             ELSE NULL END
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_agent_run_started ON public.agent_run_log(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_run_triggered ON public.agent_run_log(triggered_by);
CREATE INDEX IF NOT EXISTS idx_agent_run_alert ON public.agent_run_log(alert_level);

-- =====================================================
-- 8. SESSIONS TABLE (For authentication)
-- =====================================================
CREATE TABLE IF NOT EXISTS public.sessions (
    id              BIGSERIAL PRIMARY KEY,
    session_token   TEXT UNIQUE NOT NULL,
    worker_id       BIGINT NOT NULL REFERENCES public.workers(id) ON DELETE CASCADE,
    ip_address      TEXT,
    user_agent      TEXT,
    expires_at      TIMESTAMP NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    last_activity   TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON public.sessions(session_token);
CREATE INDEX IF NOT EXISTS idx_sessions_worker ON public.sessions(worker_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON public.sessions(expires_at);

-- =====================================================
-- FUNCTIONS & TRIGGERS
-- =====================================================

-- Auto-update updated_at function
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- Apply triggers
DROP TRIGGER IF EXISTS trg_cut_plans_updated_at ON public.cut_plans;
CREATE TRIGGER trg_cut_plans_updated_at
    BEFORE UPDATE ON public.cut_plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_cut_exec_updated_at ON public.cut_executions;
CREATE TRIGGER trg_cut_exec_updated_at
    BEFORE UPDATE ON public.cut_executions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_workers_updated_at ON public.workers;
CREATE TRIGGER trg_workers_updated_at
    BEFORE UPDATE ON public.workers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_citizen_prefs_updated_at ON public.citizen_preferences;
CREATE TRIGGER trg_citizen_prefs_updated_at
    BEFORE UPDATE ON public.citizen_preferences
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =====================================================
-- INITIAL DATA (Default admin and workers)
-- =====================================================

-- Insert default admin
INSERT INTO public.workers (worker_id, full_name, email, district, role, is_active)
VALUES ('admin', 'System Administrator', 'admin@steg.tn', 'central', 'admin', TRUE)
ON CONFLICT (worker_id) DO UPDATE SET 
    full_name = EXCLUDED.full_name,
    role = EXCLUDED.role;

-- Insert sample supervisors
INSERT INTO public.workers (worker_id, full_name, email, district, gouvernorat, role, is_active)
VALUES 
    ('sup_tunis', 'Supervisor Tunis', 'sup.tunis@steg.tn', 'Tunis Centre', 'Tunis', 'supervisor', TRUE),
    ('sup_sfax', 'Supervisor Sfax', 'sup.sfax@steg.tn', 'Sfax Ville', 'Sfax', 'supervisor', TRUE),
    ('sup_sousse', 'Supervisor Sousse', 'sup.sousse@steg.tn', 'Sousse Centre', 'Sousse', 'supervisor', TRUE)
ON CONFLICT (worker_id) DO NOTHING;

-- Insert sample workers
INSERT INTO public.workers (worker_id, full_name, email, district, gouvernorat, role, is_active)
VALUES 
    ('worker_tunis_1', 'Ahmed Ben Ali', 'ahmed.benali@steg.tn', 'Tunis Centre', 'Tunis', 'worker', TRUE),
    ('worker_sfax_1', 'Sami Makhlouf', 'sami.makhlouf@steg.tn', 'Sfax Ville', 'Sfax', 'worker', TRUE),
    ('worker_sousse_1', 'Nadia Karray', 'nadia.karray@steg.tn', 'Sousse Centre', 'Sousse', 'worker', TRUE)
ON CONFLICT (worker_id) DO NOTHING;

-- =====================================================
-- VIEWS FOR EASY ACCESS
-- =====================================================

-- Worker district assignments view
DROP VIEW IF EXISTS public.v_worker_districts CASCADE;
CREATE OR REPLACE VIEW public.v_worker_districts AS
SELECT 
    w.id,
    w.worker_id,
    w.full_name,
    w.district,
    w.gouvernorat,
    w.role,
    w.is_active,
    w.last_login,
    d.latitude,
    d.longitude,
    d.type AS district_type
FROM public.workers w
LEFT JOIN silver.districts d ON LOWER(d.district) = LOWER(w.district)
WHERE w.role IN ('worker', 'supervisor', 'admin');

-- Active cut plans view
DROP VIEW IF EXISTS public.v_active_cut_plans CASCADE;
CREATE OR REPLACE VIEW public.v_active_cut_plans AS
SELECT 
    cp.id,
    cp.plan_ref,
    cp.scheduled_date,
    cp.scheduled_start,
    cp.estimated_duration_hours,
    cp.alert_level,
    cp.status,
    cp.deficit_pct,
    cp.ai_explanation,
    cp.regions,
    cp.protected_regions,
    COUNT(DISTINCT ce.id) AS execution_count,
    COUNT(DISTINCT ce.id) FILTER (WHERE ce.status = 'restored') AS completed_count
FROM public.cut_plans cp
LEFT JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
WHERE cp.status IN ('approved', 'pending', 'executed')
GROUP BY cp.id, cp.plan_ref, cp.scheduled_date, cp.scheduled_start, 
         cp.estimated_duration_hours, cp.alert_level, cp.status, 
         cp.deficit_pct, cp.ai_explanation, cp.regions, cp.protected_regions;

-- Cut plan summary with execution stats
DROP VIEW IF EXISTS public.v_cut_plan_summary CASCADE;
CREATE OR REPLACE VIEW public.v_cut_plan_summary AS
SELECT 
    cp.id,
    cp.plan_ref,
    cp.status,
    cp.alert_level,
    cp.scheduled_date,
    cp.deficit_pct,
    COUNT(DISTINCT ce.id) AS total_executions,
    COUNT(DISTINCT ce.id) FILTER (WHERE ce.status = 'restored') AS completed_executions,
    COUNT(DISTINCT ce.id) FILTER (WHERE ce.status = 'incident') AS incident_count,
    ROUND(AVG(ce.actual_duration_minutes)::numeric, 1) AS avg_duration_minutes,
    MIN(ce.actual_start) AS first_execution,
    MAX(ce.actual_end) AS last_completion
FROM public.cut_plans cp
LEFT JOIN public.cut_executions ce ON ce.cut_plan_id = cp.id
GROUP BY cp.id, cp.plan_ref, cp.status, cp.alert_level, cp.scheduled_date, cp.deficit_pct;

-- Cut history summary view (uses existing silver.cut_history)
DROP VIEW IF EXISTS public.v_cut_history_summary CASCADE;
CREATE OR REPLACE VIEW public.v_cut_history_summary AS
SELECT 
    region,
    COUNT(*) AS total_cuts,
    SUM(duration_minutes) AS total_minutes,
    ROUND(AVG(duration_minutes)::numeric, 1) AS avg_duration_minutes,
    MAX(cut_date) AS last_cut_date,
    MIN(cut_date) AS first_cut_date,
    COUNT(DISTINCT season) AS seasons_affected
FROM silver.cut_history
GROUP BY region
ORDER BY total_cuts DESC;

-- Recent cuts view
DROP VIEW IF EXISTS public.v_recent_cuts CASCADE;
CREATE OR REPLACE VIEW public.v_recent_cuts AS
SELECT 
    id,
    region,
    cut_date,
    duration_minutes,
    reason,
    season,
    CASE 
        WHEN duration_minutes <= 30 THEN 'short'
        WHEN duration_minutes <= 120 THEN 'medium'
        ELSE 'long'
    END AS severity
FROM silver.cut_history
ORDER BY cut_date DESC
LIMIT 100;

-- =====================================================
-- DATA INTEGRITY: Link existing cut_executions to workers table
-- =====================================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name = 'cut_executions' AND column_name = 'worker_db_id') THEN
        UPDATE public.cut_executions ce
        SET worker_db_id = w.id
        FROM public.workers w
        WHERE ce.worker_id = w.worker_id
        AND ce.worker_db_id IS NULL;
    END IF;
END $$;

-- =====================================================
-- GRANTS
-- =====================================================
GRANT USAGE ON SCHEMA public TO PUBLIC;
GRANT USAGE ON SCHEMA silver TO PUBLIC;
GRANT USAGE ON SCHEMA bronze TO PUBLIC;
GRANT USAGE ON SCHEMA gold TO PUBLIC;

GRANT SELECT ON public.v_worker_districts TO PUBLIC;
GRANT SELECT ON public.v_active_cut_plans TO PUBLIC;
GRANT SELECT ON public.v_cut_plan_summary TO PUBLIC;
GRANT SELECT ON public.v_cut_history_summary TO PUBLIC;
GRANT SELECT ON public.v_recent_cuts TO PUBLIC;