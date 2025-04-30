"""User Account Controllers."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID
from structlog import get_logger
from pydantic import TypeAdapter

from litestar import Controller, delete, get, patch, post
from litestar.di import Provide
from litestar.params import Parameter
from litestar.pagination import OffsetPagination
from litestar.exceptions import PermissionDeniedException
from litestar.plugins.sqlalchemy import filters

from app.db import models as m
from app.domain.user import urls
from app.domain.user.deps import UserRepository, provide_user_details_repo
from app.domain.user.schemas import (
    User,
    UserCreate,
    UserUpdate,
)

logger = get_logger()


class UserController(Controller):
    """User Account Controller."""

    tags = ["User Accounts"]
    dependencies = {"users_repo": Provide(provide_user_details_repo)}

    @get(
        operation_id="ListUsers",
        path=urls.ACCOUNT_LIST,
    )
    async def list_users(
        self,
        users_repo: UserRepository,
        limit: int = 20,
        offset: int = 0,
    ) -> OffsetPagination[User]:
        """List users."""
        results, total = await users_repo.list_and_count(
            filters.LimitOffset(limit, offset)
        )

        type_adapter = TypeAdapter(list[User])
        return OffsetPagination[User](
            items=type_adapter.validate_python(results),
            total=total,
            limit=limit,
            offset=offset,
        )

    @get(
        operation_id="GetUser",
        path=urls.ACCOUNT_DETAIL,
    )
    async def get_user(
        self,
        users_repo: UserRepository,
        user_id: Annotated[
            UUID, Parameter(title="User ID", description="The user to retrieve.")
        ],
    ) -> User:
        """Get a user."""
        user_obj = await users_repo.get(user_id)
        return User.model_validate(user_obj)

    @post(
        operation_id="CreateUser",
        path=urls.ACCOUNT_CREATE,
    )
    async def create_user(
        self,
        data: UserCreate,
        users_repo: UserRepository,
    ) -> User:
        """Create a new user with optional bank accounts."""
        user_data = data.model_dump(exclude_none=True)
        user_data["is_active"] = True
        user_data["is_verified"] = False
        user_data["is_superuser"] = False
        logger.info("Creating user")
        logger.info(user_data)
        user_obj = await users_repo.add(user_data)
        return User.model_validate(user_obj)

    @patch(
        operation_id="UpdateUser",
        path=urls.ACCOUNT_UPDATE,
    )
    async def update_user(
        self,
        data: UserUpdate,
        users_repo: UserRepository,
        user_id: UUID = Parameter(title="User ID", description="The user to update."),
    ) -> User:
        """Update user data."""
        raw_user = data.model_dump(exclude_none=True)
        raw_user.update({"id": user_id})
        is_authenticate = await users_repo.authenticate(
            raw_user["email"], raw_user["password"]
        )
        if is_authenticate:
            raw_user.pop("password", None)
            user_obj = await users_repo.update(m.User(**raw_user))
            await users_repo.session.commit()
            return User.model_validate(user_obj)
        else:
            logger.warning("User not found or password invalid")
            raise PermissionDeniedException(detail="User not found or password invalid")

    @delete(
        operation_id="DeactivateUser",
        path=urls.ACCOUNT_DELETE,
    )
    async def deactivate_user(
        self,
        user_id: Annotated[
            UUID, Parameter(title="User ID", description="The user to delete.")
        ],
        users_repo: UserRepository,
    ) -> None:
        """Delete a user from the system."""
        user_obj = await users_repo.get(user_id)
        user_obj.is_active = False
        await users_repo.update(user_obj)
        await users_repo.session.commit()
        return None
