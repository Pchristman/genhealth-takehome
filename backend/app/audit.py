from sqlmodel import Session

from app.logging_config import request_id_var
from app.models import AuditEvent


def audit(db: Session, actor_id: str, action: str, resource_type: str, resource_id: str) -> None:
    """Add an audit row to the session. The caller commits it with the change it records."""
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id_var.get(),
        )
    )
