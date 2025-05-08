"""User Account Controllers."""

from __future__ import annotations

from sqlalchemy.orm import selectinload

from app.db import models as m
from app.domain.chat.service import ChatService, ChatMessageService
from app.lib.deps import create_service_provider


__all__ = ["provide_chat_service", "provide_chat_message_service"]

provide_chat_service = create_service_provider(
    ChatService,
    load=[selectinload(m.Chat.messages)],
    error_messages={
        "duplicate_key": "This chat already exists.",
        "integrity": "Chat operation failed.",
    },
)


provide_chat_message_service = create_service_provider(
    ChatMessageService,
    error_messages={
        "duplicate_key": "This ChatMessage already exists.",
        "integrity": "ChatMessage operation failed.",
    },
)
