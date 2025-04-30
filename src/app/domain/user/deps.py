from typing import TYPE_CHECKING, Optional, Union, Any
from app.db import models as m

from litestar.params import Parameter
from litestar.plugins.sqlalchemy import filters
from litestar.exceptions import PermissionDeniedException
from litestar.contrib.sqlalchemy.repository import SQLAlchemyAsyncRepository

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from advanced_alchemy.exceptions import ErrorMessages
from advanced_alchemy.utils.dataclass import Empty, EmptyType
from .utils import get_password_hash, verify_password

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


__all__ = [
    "UserRepository",
    "provide_user_details_repo",
    "provide_limit_offset_pagination",
]


class UserRepository(SQLAlchemyAsyncRepository[m.User]):
    model_type = m.User

    async def add(
        self,
        data: dict[str, Any] | m.User,
        *,
        auto_commit: Optional[bool] = None,
        auto_expunge: Optional[bool] = None,
        auto_refresh: Optional[bool] = None,
        error_messages: Optional[Union[ErrorMessages, EmptyType]] = Empty,
    ) -> m.User:
        """Add a new user to the database."""

        if isinstance(data, dict):
            password = data.pop("password")
            data["hashed_password"] = await get_password_hash(password)
            user_obj = m.User(**data)
        else:
            user_obj = data

        return await super().add(
            user_obj,
            auto_commit=auto_commit,
            auto_expunge=auto_expunge,
            auto_refresh=auto_refresh,
            error_messages=error_messages,
        )

    async def authenticate(self, username: str, password: bytes | str) -> m.User:
        """Authenticate a user against the stored hashed password."""
        db_obj = await self.get_one_or_none(email=username)
        if db_obj is None:
            msg = "User not found or password invalid"
            raise PermissionDeniedException(detail=msg)
        if db_obj.hashed_password is None:
            msg = "User not found or password invalid."
            raise PermissionDeniedException(detail=msg)
        if not await verify_password(password, db_obj.hashed_password):
            msg = "User not found or password invalid"
            raise PermissionDeniedException(detail=msg)
        if not db_obj.is_active:
            msg = "User account is inactive"
            raise PermissionDeniedException(detail=msg)
        return db_obj


async def provide_user_details_repo(db_session: "AsyncSession") -> UserRepository:
    """This provides a simple example demonstrating how to override the join options for the repository."""
    return UserRepository(
        statement=select(m.User).options(selectinload(m.User.roles)),
        session=db_session,
    )


def provide_limit_offset_pagination(
    current_page: int = Parameter(ge=1, query="currentPage", default=1, required=False),
    page_size: int = Parameter(
        query="pageSize",
        ge=1,
        default=10,
        required=False,
    ),
) -> filters.LimitOffset:
    """Add offset/limit pagination.

    Return type consumed by `Repository.apply_limit_offset_pagination()`.

    Parameters
    ----------
    current_page : int
        LIMIT to apply to select.
    page_size : int
        OFFSET to apply to select.
    """
    return filters.LimitOffset(page_size, page_size * (current_page - 1))
