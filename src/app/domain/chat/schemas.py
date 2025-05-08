from __future__ import annotations
from app.lib.schema import CamelizedBaseStruct

from pydantic import BaseModel

from typing import TypeVar, Generic, Literal


T = TypeVar("T")


class ToolReponse(BaseModel):
    pass


class ToolResultReponse(ToolReponse, Generic[T]):
    type: Literal[
        "VIDEO_LIST",
        "DOCUMENT_LIST",
        "VEDEO_LIST_TABLE",
        "DETAIL_TRANSCRIPT",
        "NORMAL",
    ]
    results: list[T]


class ToolMessageResponse(ToolReponse):
    type: Literal["SUCCESS", "ERROR"] = "SUCCESS"
    message: str


class ToolAuthURLResponse(ToolReponse):
    type: Literal["AUTH_URL"] = "AUTH_URL"
    url: str


class ChatMessage(CamelizedBaseStruct):
    id: str
    role: str
    message: Literal["text", "tool_call", "tool_result"]
    type: str
    created_at: str


class Chat(CamelizedBaseStruct):
    id: str
    user_id: str
    ttile: str
    token: str
    created_at: str
    updated_at: str
    messages: list[ChatMessage] = []
