"""Durable build errors and operation/worker lifecycle events."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import traceback as traceback_module
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError as PydanticValidationError


logger = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ErrorClassification:
    category: str
    recoverable: bool
    user_message: str


def classify_exception(exc: BaseException, operation: str = "unknown") -> ErrorClassification:
    """Classify by exception provenance first and operation boundary second."""
    from .llm_chunks import StageTimeoutError, StageValidationError

    if isinstance(exc, BaseExceptionGroup):
        classified=[classify_exception(child,operation) for child in exc.exceptions]
        for category in ("persistence","provider","validation","compiler","worker_runtime"):
            match=next((item for item in classified if item.category==category),None)
            if match:return match
    if isinstance(exc, (StageTimeoutError, APITimeoutError, APIConnectionError, APIStatusError)):
        return ErrorClassification("provider", True, "模型服务响应异常，已保存已完成内容。")
    if isinstance(exc, StageValidationError):
        return ErrorClassification("validation", True, "生成内容未通过数据校验，已保存已完成内容。")
    if isinstance(exc, PydanticValidationError):
        return ErrorClassification("validation", True, "生成内容与数据契约不一致，已保存已完成内容。")
    if isinstance(exc, sqlite3.Error):
        return ErrorClassification("persistence", True, "行程已生成，但保存时发生错误，可继续恢复。")
    if operation in {"assemble", "check_handoff", "profile_compile"}:
        return ErrorClassification("compiler", True, "最终旅行档案编译失败，已保存上游内容。")
    if operation in {"profile_persist", "persist_validation_error"}:
        return ErrorClassification("persistence", True, "行程已生成，但保存时发生错误，可继续恢复。")
    if operation in {"lease_acquire", "lease_heartbeat", "lease_release", "worker", "job_finalize"}:
        return ErrorClassification("worker_runtime", True, "生成任务运行状态异常，已保存已完成内容。")
    return ErrorClassification("unknown_internal", True, "生成过程中发生内部运行错误，已保存已完成内容。")


def exception_traceback(exc: BaseException) -> str:
    return "".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__))


def record_execution_event(connect, *, event_type: str, job_id: str | None = None,
                           run_id: str | None = None, worker_id: str | None = None,
                           operation: str | None = None, details: dict | None = None,
                           update_current: bool = False) -> str | None:
    event_id = str(uuid.uuid4())
    try:
        with connect() as db:
            db.execute("""INSERT INTO execution_events
              (event_id,job_id,run_id,worker_id,process_id,event_type,operation,details,created_at)
              VALUES (?,?,?,?,?,?,?,?,?)""",
              (event_id,job_id,run_id,worker_id,os.getpid(),event_type,operation,
               json.dumps(details or {},ensure_ascii=False),now()))
            if update_current and job_id:
                if run_id:
                    db.execute("UPDATE jobs SET current_operation=?,updated_at=? WHERE id=? AND run_id=?",
                               (operation,now(),job_id,run_id))
                else:
                    db.execute("UPDATE jobs SET current_operation=?,updated_at=? WHERE id=?",
                               (operation,now(),job_id))
        return event_id
    except Exception:
        logger.exception("failed to persist execution event", extra={"job_id":job_id,"run_id":run_id,
                         "operation":operation,"event_type":event_type})
        return None


def record_error_event(connect, exc: BaseException, *, job_id: str, run_id: str | None,
                       stage: str, operation: str, pack_id: str | None = None,
                       substage: str | None = None, provider_request_id: str | None = None,
                       worker_id: str | None = None) -> tuple[str | None, ErrorClassification]:
    classification=classify_exception(exc,operation)
    trace=exception_traceback(exc)
    developer_error=f"{type(exc).__name__}: {exc}\n{trace}"
    error_id=str(uuid.uuid4())
    logger.exception("build failed", exc_info=(type(exc),exc,exc.__traceback__),
                     extra={"job_id":job_id,"run_id":run_id,"stage":stage,
                            "pack_id":pack_id,"substage":substage,"operation":operation,
                            "error_category":classification.category,"error_id":error_id})
    try:
        with connect() as db:
            db.execute("""INSERT INTO error_events
              (error_id,job_id,run_id,pack_id,substage,stage,operation,exception_type,
               exception_message,traceback,provider_request_id,created_at,recoverable,error_category,
               user_message,developer_error,worker_id,process_id)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (error_id,job_id,run_id,pack_id,substage,stage,operation,type(exc).__name__,str(exc),trace,
               provider_request_id,now(),int(classification.recoverable),classification.category,
               classification.user_message,developer_error,worker_id,os.getpid()))
            db.execute("UPDATE jobs SET last_error_event_id=?,current_operation=?,updated_at=? WHERE id=?",
                       (error_id,operation,now(),job_id))
        return error_id,classification
    except Exception:
        logger.exception("failed to persist error event", extra={"job_id":job_id,"run_id":run_id,
                         "stage":stage,"pack_id":pack_id,"operation":operation})
        return None,classification
