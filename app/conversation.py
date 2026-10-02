"""DB-backed chat history for webhook multi-turn context."""

from sqlalchemy.orm import Session

from app.models import ConversationMessage, MessageRole

HISTORY_LIMIT = 16


def already_processed(db: Session, wa_message_id: str | None) -> bool:
    if not wa_message_id:
        return False
    return (
        db.query(ConversationMessage)
        .filter(ConversationMessage.wa_message_id == wa_message_id)
        .first()
        is not None
    )


def load_history(db: Session, user_id: int, limit: int = HISTORY_LIMIT) -> list[dict]:
    rows = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.user_id == user_id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [{"role": row.role.value, "content": row.content} for row in rows]


def append_turn(
    db: Session,
    user_id: int,
    user_text: str,
    assistant_text: str,
    wa_message_id: str | None = None,
) -> None:
    db.add(
        ConversationMessage(
            user_id=user_id,
            role=MessageRole.user,
            content=user_text,
            wa_message_id=wa_message_id,
        )
    )
    db.add(
        ConversationMessage(
            user_id=user_id,
            role=MessageRole.assistant,
            content=assistant_text,
            wa_message_id=None,
        )
    )
    db.commit()
