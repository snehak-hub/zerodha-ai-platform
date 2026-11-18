"""PostgreSQL-backed storage for jobs, insights, audits, feedback and chat history (all per-user)."""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, delete
from sqlalchemy.orm import Session

from backend.db.models import AnalysisJob, AuditLog, ChatMessage, Feedback, Insight


def create_job(db: Session, user_id: int, portfolio_id: str, timeframe: str = "1M") -> str:
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    db.add(AnalysisJob(id=job_id, user_id=user_id, portfolio_id=portfolio_id, timeframe=timeframe))
    db.commit()
    return job_id


def update_job_status(db: Session, job_id: str, status: str, error: Optional[str] = None) -> None:
    job = db.get(AnalysisJob, job_id)
    if not job:
        return
    job.status = status
    if error:
        job.error_message = error
    if status in ("COMPLETED", "FAILED"):
        job.completed_at = datetime.now(timezone.utc)
    db.commit()


def get_job(db: Session, job_id: str, user_id: int) -> Optional[AnalysisJob]:
    """Returns the job only if it belongs to the user (prevents reading other users' results)."""
    job = db.get(AnalysisJob, job_id)
    return job if job and job.user_id == user_id else None


def save_insights(db: Session, job_id: str, payload: Dict[str, Any]) -> None:
    db.merge(Insight(job_id=job_id, payload=payload))
    db.commit()


def get_insights(db: Session, job_id: str) -> Optional[Dict[str, Any]]:
    row = db.get(Insight, job_id)
    return row.payload if row else None


def record_audit(db: Session, job_id: str, payload: Dict[str, Any]) -> None:
    db.merge(AuditLog(job_id=job_id, payload=payload))
    db.commit()


def get_audit(db: Session, job_id: str) -> Optional[Dict[str, Any]]:
    row = db.get(AuditLog, job_id)
    return row.payload if row else None


def record_feedback(db: Session, user_id: int, card_id: str, user_action: str, comments: Optional[str] = None) -> None:
    db.add(Feedback(user_id=user_id, card_id=card_id, user_action=user_action, comments=comments))
    db.commit()


def job_counts(db: Session, user_id: int) -> Dict[str, int]:
    rows = db.execute(select(AnalysisJob.status, func.count()).where(AnalysisJob.user_id == user_id)
                      .group_by(AnalysisJob.status)).all()
    counts = {status: n for status, n in rows}
    return {"total": sum(counts.values()), "completed": counts.get("COMPLETED", 0), "failed": counts.get("FAILED", 0)}


def feedback_count(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Feedback).where(Feedback.user_id == user_id)) or 0


# ---- chat history ----
def add_chat_message(db: Session, user_id: int, role: str, content: str) -> None:
    db.add(ChatMessage(user_id=user_id, role=role, content=content))
    db.commit()


def recent_chat(db: Session, user_id: int, limit: int = 10) -> List[Dict[str, str]]:
    rows = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user_id)
                      .order_by(ChatMessage.id.desc()).limit(limit)).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def clear_chat(db: Session, user_id: int) -> None:
    db.execute(delete(ChatMessage).where(ChatMessage.user_id == user_id))
    db.commit()
