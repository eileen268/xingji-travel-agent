"""Small, explicit SQLite migration runner with pre-migration backups."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (1, "initial_jobs_and_packs", """
      CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT NOT NULL,
        progress INTEGER NOT NULL, preferences TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        result TEXT, packs TEXT, report TEXT, error TEXT
      );
      CREATE TABLE IF NOT EXISTS job_packs (
        job_id TEXT NOT NULL, pack_id TEXT NOT NULL, status TEXT NOT NULL,
        payload TEXT, validation_errors TEXT,
        generation_attempt_count INTEGER NOT NULL DEFAULT 0,
        repair_attempt_count INTEGER NOT NULL DEFAULT 0,
        resume_count INTEGER NOT NULL DEFAULT 0,
        input_signature TEXT, prompt_version TEXT, schema_version TEXT,
        pipeline_version TEXT, updated_at TEXT NOT NULL,
        PRIMARY KEY(job_id,pack_id)
      );
    """),
    (2, "execution_leases_and_manifest", """
      ALTER TABLE jobs ADD COLUMN run_id TEXT;
      ALTER TABLE jobs ADD COLUMN lock_owner TEXT;
      ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT;
      ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT;
      ALTER TABLE jobs ADD COLUMN execution_attempt_count INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'REAL_LLM';
      ALTER TABLE jobs ADD COLUMN source_job_id TEXT;
      ALTER TABLE jobs ADD COLUMN replay_from TEXT;
      ALTER TABLE jobs ADD COLUMN manifest TEXT;
      ALTER TABLE jobs ADD COLUMN idempotency_key TEXT;
      ALTER TABLE jobs ADD COLUMN request_signature TEXT;
      CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency_key ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;
      CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, created_at);
    """),
    (3, "pack_observability", """
      ALTER TABLE job_packs ADD COLUMN started_at TEXT;
      ALTER TABLE job_packs ADD COLUMN finished_at TEXT;
      ALTER TABLE job_packs ADD COLUMN duration_ms INTEGER;
      ALTER TABLE job_packs ADD COLUMN model_request_id TEXT;
      ALTER TABLE job_packs ADD COLUMN token_input INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE job_packs ADD COLUMN token_output INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE job_packs ADD COLUMN repair_result TEXT;
      ALTER TABLE job_packs ADD COLUMN manifest TEXT;
      ALTER TABLE job_packs ADD COLUMN source_payload TEXT;
      ALTER TABLE job_packs ADD COLUMN run_id TEXT;
    """),
    (4, "historical_manifest_backfill", """
      UPDATE jobs SET manifest='{"mode":"LEGACY","model_name":null,"model_version":"unknown","prompt_version":"unknown","pipeline_version":"unknown","schema_version":"seven-v1","validator_version":"unknown","scheduler_version":"unknown","allocator_version":"unknown","created_at":"unknown"}' WHERE manifest IS NULL;
      UPDATE job_packs SET manifest=(SELECT jobs.manifest FROM jobs WHERE jobs.id=job_packs.job_id) WHERE manifest IS NULL;
    """),
    (5, "llm_subrequest_observability", """
      CREATE TABLE IF NOT EXISTS llm_subrequests (
        job_id TEXT NOT NULL,
        subrequest_id TEXT NOT NULL,
        pack_id TEXT NOT NULL,
        substage TEXT NOT NULL,
        run_id TEXT,
        status TEXT NOT NULL,
        model_name TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        elapsed_ms INTEGER,
        prompt_chars INTEGER NOT NULL,
        estimated_tokens INTEGER NOT NULL,
        generation_attempt_number INTEGER NOT NULL,
        repair_attempt_number INTEGER NOT NULL,
        provider_request_id TEXT,
        error_type TEXT,
        error_status_code INTEGER,
        PRIMARY KEY(job_id,subrequest_id)
      );
      CREATE INDEX IF NOT EXISTS idx_llm_subrequests_job_status ON llm_subrequests(job_id,status);
      CREATE INDEX IF NOT EXISTS idx_llm_subrequests_job_pack ON llm_subrequests(job_id,pack_id);
    """),
    (6, "provider_retry_and_subrequest_checkpoints", """
      ALTER TABLE llm_subrequests ADD COLUMN checkpoint_key TEXT;
      ALTER TABLE llm_subrequests ADD COLUMN payload TEXT;
      ALTER TABLE llm_subrequests ADD COLUMN token_input INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE llm_subrequests ADD COLUMN token_output INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE llm_subrequests ADD COLUMN provider_retry_count INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE llm_subrequests ADD COLUMN max_tokens INTEGER;
      ALTER TABLE llm_subrequests ADD COLUMN target_item_count INTEGER;
      ALTER TABLE llm_subrequests ADD COLUMN expected_output_tokens INTEGER;
      ALTER TABLE job_packs ADD COLUMN provider_retry_count INTEGER NOT NULL DEFAULT 0;
      CREATE INDEX IF NOT EXISTS idx_llm_checkpoint ON llm_subrequests(job_id,pack_id,substage,checkpoint_key,status);
    """),
    (7, "error_and_worker_observability", """
      ALTER TABLE jobs ADD COLUMN current_operation TEXT;
      ALTER TABLE jobs ADD COLUMN last_error_event_id TEXT;
      CREATE TABLE IF NOT EXISTS error_events (
        error_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        run_id TEXT,
        pack_id TEXT,
        substage TEXT,
        stage TEXT NOT NULL,
        operation TEXT NOT NULL,
        exception_type TEXT NOT NULL,
        exception_message TEXT NOT NULL,
        traceback TEXT NOT NULL,
        provider_request_id TEXT,
        created_at TEXT NOT NULL,
        recoverable INTEGER NOT NULL,
        error_category TEXT NOT NULL,
        user_message TEXT NOT NULL,
        developer_error TEXT NOT NULL,
        worker_id TEXT,
        process_id INTEGER
      );
      CREATE INDEX IF NOT EXISTS idx_error_events_job_created ON error_events(job_id,created_at);
      CREATE TABLE IF NOT EXISTS execution_events (
        event_id TEXT PRIMARY KEY,
        job_id TEXT,
        run_id TEXT,
        worker_id TEXT,
        process_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        operation TEXT,
        details TEXT,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_execution_events_job_created ON execution_events(job_id,created_at);
    """),
    (8, "canonical_places_and_amap_resolution", """
      CREATE TABLE IF NOT EXISTS canonical_places (
        place_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        provider_place_id TEXT,
        canonical_name TEXT NOT NULL,
        local_name TEXT,
        english_name TEXT,
        display_name TEXT NOT NULL,
        city TEXT NOT NULL,
        district TEXT,
        address TEXT,
        latitude REAL,
        longitude REAL,
        category TEXT NOT NULL,
        subcategory TEXT,
        place_type TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        source_confidence REAL,
        semantic_tags TEXT NOT NULL,
        interest_affinity TEXT NOT NULL,
        source_metadata TEXT NOT NULL,
        fact_provenance TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(provider,provider_place_id)
      );
      CREATE INDEX IF NOT EXISTS idx_canonical_places_city_type ON canonical_places(city,place_type);
      CREATE TABLE IF NOT EXISTS place_resolutions (
        resolution_id TEXT PRIMARY KEY,
        job_id TEXT,
        pack_id TEXT,
        generated_place_id TEXT NOT NULL,
        generated_name TEXT NOT NULL,
        city TEXT NOT NULL,
        selected_place_id TEXT,
        provider_place_id TEXT,
        score REAL,
        status TEXT NOT NULL,
        candidates_json TEXT NOT NULL,
        error_code TEXT,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_place_resolutions_job_pack ON place_resolutions(job_id,pack_id);
      CREATE TABLE IF NOT EXISTS provider_cache (
        provider TEXT NOT NULL,
        operation TEXT NOT NULL,
        cache_key TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        PRIMARY KEY(provider,operation,cache_key)
      );
    """),
    (9, "routes_and_schedule_runs", """
      CREATE TABLE IF NOT EXISTS routes (
        origin_place_id TEXT NOT NULL,
        destination_place_id TEXT NOT NULL,
        mode TEXT NOT NULL,
        distance_meters INTEGER,
        duration_seconds INTEGER,
        provider TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        provider_route_id TEXT,
        warning TEXT,
        queried_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        PRIMARY KEY(origin_place_id,destination_place_id,mode)
      );
      CREATE INDEX IF NOT EXISTS idx_routes_status ON routes(verification_status,queried_at);
      CREATE TABLE IF NOT EXISTS schedule_runs (
        schedule_run_id TEXT PRIMARY KEY,
        job_id TEXT,
        pack_id TEXT NOT NULL,
        day_number INTEGER NOT NULL,
        scheduler_version TEXT NOT NULL,
        input_signature TEXT NOT NULL,
        status TEXT NOT NULL,
        warnings TEXT NOT NULL,
        payload TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_schedule_runs_job_day ON schedule_runs(job_id,day_number,started_at);
    """),
    (10, "fact_verification_runs", """
      CREATE TABLE IF NOT EXISTS fact_verification_runs (
        verification_run_id TEXT PRIMARY KEY,
        job_id TEXT,
        run_id TEXT,
        verifier_version TEXT NOT NULL,
        status TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_fact_verification_job ON fact_verification_runs(job_id,started_at);
    """),
    (11, "official_fact_resolution", """
      ALTER TABLE canonical_places ADD COLUMN official_facts TEXT NOT NULL DEFAULT '{}';
      CREATE TABLE IF NOT EXISTS official_source_resolutions (
        resolution_id TEXT PRIMARY KEY,
        job_id TEXT,
        place_id TEXT NOT NULL,
        status TEXT NOT NULL,
        official_url TEXT,
        source_type TEXT NOT NULL,
        confidence REAL NOT NULL,
        resolution_method TEXT NOT NULL,
        candidates_json TEXT NOT NULL,
        resolved_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_official_resolution_job_place ON official_source_resolutions(job_id,place_id,resolved_at);
      CREATE TABLE IF NOT EXISTS official_page_cache (
        url TEXT PRIMARY KEY,
        page_type TEXT NOT NULL,
        final_url TEXT NOT NULL,
        http_status INTEGER,
        content_hash TEXT,
        content TEXT,
        fetched_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        error TEXT
      );
    """),
    (12, "official_source_discovery", """
      CREATE TABLE IF NOT EXISTS official_sources (
        place_id TEXT NOT NULL,
        url TEXT NOT NULL,
        domain TEXT NOT NULL,
        source_type TEXT NOT NULL,
        status TEXT NOT NULL,
        confidence REAL NOT NULL,
        discovery_provider TEXT NOT NULL,
        discovery_query TEXT,
        search_rank INTEGER,
        resolved_at TEXT NOT NULL,
        last_verified_at TEXT,
        expires_at TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(place_id,url)
      );
      CREATE INDEX IF NOT EXISTS idx_official_sources_place_status ON official_sources(place_id,status,expires_at);
      CREATE TABLE IF NOT EXISTS official_search_logs (
        request_id TEXT PRIMARY KEY,
        place_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        query TEXT NOT NULL,
        status TEXT NOT NULL,
        result_count INTEGER NOT NULL,
        latency_ms REAL NOT NULL,
        cache_hit INTEGER NOT NULL,
        error TEXT,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_official_search_place ON official_search_logs(place_id,created_at);
    """),
    (13, "local_replan_runs", """
      CREATE TABLE IF NOT EXISTS trip_replan_runs (
        replan_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        idempotency_key TEXT,
        action TEXT NOT NULL,
        day_number INTEGER,
        request_json TEXT NOT NULL,
        status TEXT NOT NULL,
        affected_packs_json TEXT NOT NULL,
        before_result TEXT,
        after_result TEXT,
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        completed_at TEXT
      );
      CREATE UNIQUE INDEX IF NOT EXISTS idx_replan_idempotency
        ON trip_replan_runs(job_id,idempotency_key) WHERE idempotency_key IS NOT NULL;
      CREATE INDEX IF NOT EXISTS idx_replan_job_created ON trip_replan_runs(job_id,created_at);
    """),
    (14, "editable_replanning", """
      CREATE TABLE IF NOT EXISTS trip_user_places (
        job_id TEXT NOT NULL,
        place_id TEXT NOT NULL,
        day_number INTEGER NOT NULL,
        place_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(job_id,place_id)
      );
      CREATE INDEX IF NOT EXISTS idx_user_places_job_day ON trip_user_places(job_id,day_number);
      CREATE TABLE IF NOT EXISTS trip_day_edit_state (
        job_id TEXT NOT NULL,
        day_number INTEGER NOT NULL,
        stop_overrides_json TEXT NOT NULL DEFAULT '{}',
        day_transport TEXT,
        segment_overrides_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL,
        PRIMARY KEY(job_id,day_number)
      );
      CREATE TABLE IF NOT EXISTS trip_day_revisions (
        revision_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        day_number INTEGER NOT NULL,
        version INTEGER NOT NULL,
        action TEXT NOT NULL,
        initiated_by TEXT NOT NULL,
        instruction_text TEXT,
        parsed_constraints_json TEXT,
        before_state_json TEXT NOT NULL,
        after_state_json TEXT NOT NULL,
        diff_json TEXT NOT NULL,
        scheduler_version TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE UNIQUE INDEX IF NOT EXISTS idx_day_revision_version ON trip_day_revisions(job_id,day_number,version);
      CREATE INDEX IF NOT EXISTS idx_day_revision_latest ON trip_day_revisions(job_id,day_number,created_at);
      CREATE TABLE IF NOT EXISTS trip_replan_previews (
        preview_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        day_number INTEGER NOT NULL,
        action TEXT NOT NULL,
        request_json TEXT NOT NULL,
        proposed_state_json TEXT NOT NULL,
        diff_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        confirmed_at TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_replan_preview_job ON trip_replan_previews(job_id,status,expires_at);
    """),
    (15, "route_diagnostics", """
      ALTER TABLE routes ADD COLUMN origin_latitude REAL;
      ALTER TABLE routes ADD COLUMN origin_longitude REAL;
      ALTER TABLE routes ADD COLUMN destination_latitude REAL;
      ALTER TABLE routes ADD COLUMN destination_longitude REAL;
      ALTER TABLE routes ADD COLUMN http_status INTEGER;
      ALTER TABLE routes ADD COLUMN provider_response_code TEXT;
      ALTER TABLE routes ADD COLUMN route_error TEXT;
    """),
    (16, "context_aware_place_resolution", """
      ALTER TABLE place_resolutions ADD COLUMN queries_json TEXT NOT NULL DEFAULT '[]';
      ALTER TABLE place_resolutions ADD COLUMN context_json TEXT;
    """),
    (17, "scheduled_resolution_diagnostics", """
      ALTER TABLE place_resolutions ADD COLUMN resolution_reason TEXT;
    """),
)


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _apply_statement(db: sqlite3.Connection, statement: str):
    statement = statement.strip()
    if not statement:
        return
    upper = statement.upper()
    if upper.startswith("ALTER TABLE") and " ADD COLUMN " in upper:
        parts = statement.split()
        table, column = parts[2], parts[5]
        if column in _columns(db, table):
            return
    db.execute(statement)


def backup_database(database_path: Path, target_version: int) -> Path | None:
    if not database_path.exists() or database_path.stat().st_size == 0:
        return None
    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{database_path.stem}-before-v{target_version}-{stamp}.db"
    source = sqlite3.connect(database_path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close(); source.close()
    return target


def migrate(database_path: Path) -> list[int]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database_path, timeout=15)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )""")
        db.commit()
        applied = {r[0]: r[1] for r in db.execute("SELECT version,checksum FROM schema_migrations")}
        pending = [(v, n, s) for v, n, s in MIGRATIONS if v not in applied]
        for version, name, sql in MIGRATIONS:
            if version in applied and applied[version] != _checksum(sql):
                raise RuntimeError(f"migration checksum mismatch: {version}")
        if pending:
            backup_database(database_path, pending[0][0])
        completed = []
        for version, name, sql in pending:
            try:
                db.execute("BEGIN IMMEDIATE")
                for statement in sql.split(";"):
                    _apply_statement(db, statement)
                db.execute("INSERT INTO schema_migrations VALUES (?,?,?,?)",
                           (version, name, _checksum(sql), datetime.now(timezone.utc).isoformat()))
                db.commit(); completed.append(version)
            except BaseException:
                db.rollback(); raise
        return completed
    finally:
        db.close()


def migration_status(database_path: Path) -> dict:
    if not database_path.exists():
        return {"current_version": 0, "latest_version": MIGRATIONS[-1][0], "pending": [v for v, _, _ in MIGRATIONS]}
    db = sqlite3.connect(database_path)
    try:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        applied = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")] if exists else []
    finally:
        db.close()
    return {"current_version": max(applied, default=0), "latest_version": MIGRATIONS[-1][0],
            "pending": [v for v, _, _ in MIGRATIONS if v not in applied]}
