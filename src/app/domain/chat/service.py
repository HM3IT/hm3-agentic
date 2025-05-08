from __future__ import annotations

from uuid import UUID
from typing import Any, cast
from structlog import getLogger

from pydantic import ModelDictT, ModelT
from litestar.exceptions import NotFoundException

from advanced_alchemy.repository import SQLAlchemyAsyncSlugRepository
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService
from advanced_alchemy.exceptions import ErrorMessages
from advanced_alchemy.utils.dataclass import Empty, EmptyType

from app.domain.chat.schemas import AddChatMessage
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


    async def create(
        self,
        data: ModelDictT[AddChatMessage],
        *,
        auto_commit: bool | None = None,
        auto_expunge: bool | None = None,
        auto_refresh: bool | None = None,
        error_messages: ErrorMessages | type[Empty] | None = None,
    ) -> m.ChatMessage:
        """Wrap repository instance creation.

        Args:
            data: Representation to be created.
            auto_expunge: Remove object from session before returning.
            auto_refresh: Refresh object from session before returning.
            auto_commit: Commit objects before returning.
            error_messages: An optional dictionary of templates to use
                for friendlier error messages to clients
        data = await self.to_model(data, "create")  # Ensure data is converted to the correct model type
        Returns:
            Representation of created instance.
        """
        data = await self.to_model(data, "create")
        return cast(
            "ModelT",
            await self.repository.add(
                data=data,
                auto_commit=auto_commit,
                auto_expunge=auto_expunge,
                auto_refresh=auto_refresh,
                error_messages=error_messages,
            ),
        )