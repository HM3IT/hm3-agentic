from __future__ import annotations

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel as _BaseModel



__all__ = (
    "AccountLogin",
    "AccountRegister",
    "User",
    "UserCreate",
    "UserRole",
    "UserRoleAdd",
    "UserRoleRevoke",
    "UserTeam",
    "UserUpdate",
)

class BaseModel(_BaseModel):
    """Extend Pydantic's BaseModel to enable ORM mode"""

    model_config = {"from_attributes": True}



class UserTeam(BaseModel):
    """Holds team details for a user.

    This is nested in the User Model for 'team'
    """

    team_id: UUID
    team_name: str
    is_owner: bool = False


class Role(BaseModel):
    id: UUID
    name: str
    slug: str


class UserRole(BaseModel):
    """Holds role details for a user.

    This is nested in the User Model for 'roles'
    """

    role_id: UUID
    role_slug: str
    role_name: str
    assigned_at: datetime
 

class User(BaseModel):
    """User properties to use for a response."""

    id: UUID
    email: str
    name: str | None = None
    avatar_url: str | None = None
    is_superuser: bool = False
    is_active: bool = True
    is_verified: bool = False
    roles: list[UserRole] = []


class UserCreate(BaseModel):
    email: str
    password: str
    name: str | None = None
    is_superuser: bool = False
    avatar_url: str | None = None


class UserUpdate(BaseModel):
    password: str
    email: str
    name: str | None = None
    avatar_url: str | None = None
    is_superuser: bool | None = None
    is_active: bool | None = None
    is_verified: bool | None = None
  

class UserUpdatePassword(BaseModel):
    current_password: str
    new_password: str


class AccountLogin(BaseModel):
    username: str
    password: str


class AccountRegister(BaseModel):
    email: str
    password: str
    name: str | None = None


class UserRoleAdd(BaseModel):
    """User role add ."""

    user_name: str


class UserRoleRevoke(BaseModel):
    """User role revoke ."""

    user_name: str
