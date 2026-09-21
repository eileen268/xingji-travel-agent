"""Atomic job claims and renewable SQLite worker leases."""
from __future__ import annotations

import os
import socket
import uuid
import json
from datetime import datetime, timedelta, timezone


LEASE_SECONDS = int(os.getenv("WORKER_LEASE_SECONDS", "45"))
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def utcnow(): return datetime.now(timezone.utc)
def stamp(value=None): return (value or utcnow()).isoformat()


def recover_expired(connect) -> int:
    with connect() as db:
        expired=db.execute("""SELECT id,run_id,lock_owner,lease_expires_at FROM jobs
            WHERE status IN ('running','repairing','validating')
            AND (lease_expires_at IS NULL OR lease_expires_at < ?)""",(stamp(),)).fetchall()
        cursor=db.execute("""UPDATE jobs SET status='queued',stage='queued',run_id=NULL,lock_owner=NULL,
            lease_expires_at=NULL,current_operation='lease_recovery' WHERE status IN ('running','repairing','validating')
            AND (lease_expires_at IS NULL OR lease_expires_at < ?)""",(stamp(),))
        db.execute("UPDATE job_packs SET status='pending' WHERE status='running' AND job_id IN (SELECT id FROM jobs WHERE status='queued')")
        for row in expired:
            db.execute("""INSERT INTO execution_events
              (event_id,job_id,run_id,worker_id,process_id,event_type,operation,details,created_at)
              VALUES (?,?,?,?,?,?,?,?,?)""",(str(uuid.uuid4()),row['id'],row['run_id'],row['lock_owner'],os.getpid(),
              'lease_expired_recovered','lease_recovery',json.dumps({'resume_reason':'lease_expired','previous_lease_expires_at':row['lease_expires_at']}),stamp()))
        return cursor.rowcount


def claim_next(connect, worker_id: str = WORKER_ID):
    """Claim exactly one queued job. BEGIN IMMEDIATE serializes competing workers."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not row:
            return None
        run_id=str(uuid.uuid4()); now=utcnow(); lease=now+timedelta(seconds=LEASE_SECONDS)
        changed=db.execute("""UPDATE jobs SET status='running',stage='starting',run_id=?,lock_owner=?,heartbeat_at=?,
            lease_expires_at=?,execution_attempt_count=execution_attempt_count+1,current_operation='lease_acquire',updated_at=? WHERE id=? AND status='queued'""",
            (run_id,worker_id,stamp(now),stamp(lease),stamp(now),row['id'])).rowcount
        if changed != 1:
            return None
        db.execute("""INSERT INTO execution_events
          (event_id,job_id,run_id,worker_id,process_id,event_type,operation,details,created_at)
          VALUES (?,?,?,?,?,?,?,?,?)""",(str(uuid.uuid4()),row['id'],run_id,worker_id,os.getpid(),'lease_acquired',
          'lease_acquire',json.dumps({'resume_reason':row['stage'] if row['stage']!='queued' else 'queued'}),stamp(now)))
        return dict(row)|{'run_id':run_id,'lock_owner':worker_id,'status':'running'}


def heartbeat(connect, job_id: str, run_id: str, worker_id: str = WORKER_ID) -> bool:
    now=utcnow(); lease=now+timedelta(seconds=LEASE_SECONDS)
    with connect() as db:
        changed=db.execute("""UPDATE jobs SET heartbeat_at=?,lease_expires_at=?,updated_at=?
            WHERE id=? AND run_id=? AND lock_owner=? AND status IN ('running','repairing','validating')""",
            (stamp(now),stamp(lease),stamp(now),job_id,run_id,worker_id)).rowcount
        if changed != 1:
            db.execute("""INSERT INTO execution_events
              (event_id,job_id,run_id,worker_id,process_id,event_type,operation,details,created_at)
              VALUES (?,?,?,?,?,?,?,?,?)""",(str(uuid.uuid4()),job_id,run_id,worker_id,os.getpid(),'lease_lost',
              'lease_heartbeat',json.dumps({'reason':'heartbeat_update_rejected'}),stamp(now)))
    return changed == 1


def release(connect, job_id: str, run_id: str, **fields) -> bool:
    fields.update(run_id=None,lock_owner=None,lease_expires_at=None,heartbeat_at=None,updated_at=stamp())
    with connect() as db:
        sql='UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=? AND run_id=?'
        changed=db.execute(sql,[*fields.values(),job_id,run_id]).rowcount == 1
        db.execute("""INSERT INTO execution_events
          (event_id,job_id,run_id,worker_id,process_id,event_type,operation,details,created_at)
          VALUES (?,?,?,?,?,?,?,?,?)""",(str(uuid.uuid4()),job_id,run_id,WORKER_ID,os.getpid(),
          'lease_released' if changed else 'lease_release_rejected','lease_release',
          json.dumps({'target_status':fields.get('status'),'changed':changed}),stamp()))
        return changed
