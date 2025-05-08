from __future__ import annotations

from uuid import UUID
from typing import Any
from structlog import getLogger

from litestar.exceptions import NotFoundException

from advanced_alchemy.repository import SQLAlchemyAsyncSlugRepository
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService

from app.db import models as m


__all__ = ["ChatService", "ChatMessageService"]

logger = getLogger()


class ChatService(SQLAlchemyAsyncRepositoryService[m.Chat]):
    """Handles database operations for chats."""

    class ChatRepository(SQLAlchemyAsyncSlugRepository[m.Chat]):
        """Chat SQLAlchemy Repository."""

        model_type = m.Chat

    repository_type = ChatRepository

    def __init__(self, **repo_kwargs: Any) -> None:

        super().__init__(**repo_kwargs)

        self.model_type = self.repository.model_type
        
    async def update_title(self, chat_id: UUID, title: str) -> m.Chat:
        chat = self.repository.get_one_or_none(id=chat_id)
        if not chat:
            raise NotFoundException(detail="Chat not found", status_code=404)
        chat.title = title
        return self.repository.update(item=chat)


class ChatMessageService(SQLAlchemyAsyncRepositoryService[m.ChatMessage]):
    """Handles database operations for chatMessage."""

    class ChatMessageRepository(SQLAlchemyAsyncSlugRepository[m.ChatMessage]):
        """ChatMessage SQLAlchemy Repository."""

        model_type = m.ChatMessage

    repository_type = ChatMessageRepository

    def __init__(self, **repo_kwargs: Any) -> None:

        super().__init__(**repo_kwargs)

        self.model_type = self.repository.model_type
