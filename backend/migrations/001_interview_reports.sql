-- Interview report persistence for voice + text sessions

CREATE TABLE IF NOT EXISTS interview_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(64) NOT NULL UNIQUE,
    candidate_name VARCHAR(255) NOT NULL DEFAULT 'Candidate',
    job_role VARCHAR(255) NOT NULL DEFAULT '',
    experience_level VARCHAR(32) NOT NULL DEFAULT 'mid',
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    transcript JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_interview_reports_session_id ON interview_reports(session_id);
CREATE INDEX IF NOT EXISTS idx_interview_reports_created_at ON interview_reports(created_at DESC);
