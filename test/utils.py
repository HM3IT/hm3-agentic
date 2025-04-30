import os
import json
import aiofile
import httpx

from dotenv import load_dotenv
from uuid import UUID
from typing import Any, Annotated, Literal
from pathlib import Path
from structlog import getLogger
from datetime import datetime, timezone

from collections.abc import AsyncGenerator
 
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from autogen_core import CancellationToken
from autogen_core.models import FunctionExecutionResultMessage
from autogen_core.memory import ListMemory, MemoryContent, MemoryMimeType

from autogen_agentchat.teams import SelectorGroupChat
from autogen_agentchat.messages import (
    ToolCallExecutionEvent,
    ToolCallRequestEvent,
    MemoryQueryEvent,
    ThoughtEvent,
    TextMessage,
    ToolCallSummaryMessage,
)
from autogen_agentchat.conditions import ExternalTermination

from litestar.serialization import encode_json
 
__all__ = [
    "load_sessions",
    "save_sessions",
    "add_chat_entry",
    "get_token",
    "update_token",
    "load_chat_history",
    "get_chat_history",
]

load_dotenv()
logger = getLogger()
 

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

API_BASE_URL = os.environ["API_BASE_URL"]
USER_AGENT = os.environ["USER_AGENT_NAME"]
session_file_path = os.environ["CHAT_HISTORY_FOLDER_PATH"] + "/sessions.json"
team_dir_path = os.environ["CHAT_HISTORY_FOLDER_PATH"]
download_dir_path = os.environ["DOWNLOAD_FOLDER_PATH"]
token_filepath = os.environ["TOKEN_FILEPATH"]
clinet_secrets_filepath = os.environ["CLIENT_SECRETS_FILEPATH"]


async def load_sessions():
    if os.path.exists(session_file_path):
        async with aiofile.async_open(session_file_path, "r") as f:
            data = await f.read()
            return json.loads(data)
    return {}


async def save_sessions(sessions):
    logger.info("saving data")
    async with aiofile.async_open(session_file_path, "w") as f:
        await f.write(json.dumps(sessions, indent=2))


async def add_chat_entry(
    session_id: str,
    source: str,
    message: str,
    message_type: Literal["text", "tool_call", "tool_result"],
) -> None:
    sessions = await load_sessions()

    if session_id not in sessions:
        sessions[session_id] = {"token": None, "chat_history": []}
    sessions[session_id]["chat_history"].append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": source,
            "message": message,
            "type": message_type,
        }
    )
    await save_sessions(sessions)


async def get_token(
    session_id: Annotated[UUID, "The chat session ID"], key: str = "token"
) -> str | None:
    str_session_id = str(session_id)
    sessions = await load_sessions()
    if str_session_id in sessions:
        return sessions[str_session_id].get(key)
    return None


async def update_token(
    session_id: str,
    token: str,
    refresh_token: str | None = None,
) -> str:
    sessions = await load_sessions()
    if session_id not in sessions:
        sessions[session_id] = {"token": token, "chat_history": []}
    else:
        sessions[session_id]["token"] = token
        sessions[session_id]["refresh_token"] = refresh_token
    await save_sessions(sessions)
    return "Authentication successful"


async def get_chat_history(
    session_id: Annotated[UUID, "The chat session ID"],
) -> dict[str, Any]:
    history = {"token": None, "chat_history": []}

    if os.path.exists(session_file_path):
        data = json.loads(Path(session_file_path).read_text())
        history = data.get(str(session_id), {"token": None, "chat_history": []})
    return history


async def get_team_state(
    session_id: Annotated[UUID, "The chat session ID"],
) -> dict[str, Any]:
    state_file_path = f"{team_dir_path}/{session_id}.json"
    if os.path.exists(state_file_path):
        return json.loads(Path(state_file_path).read_text())

    return {}


async def load_chat_history(
    session_id: Annotated[UUID, "The chat session ID"], user_memory: "ListMemory"
) -> "ListMemory":
    """
    Reloads the chat history for a given session_id from a JSON file, and populates
    the user_memory with the history.

    Args:
        session_id (str): the session_id to load the history for.
        user_memory (ListMemory): the memory to populate with the history.

    Returns:
        ListMemory: the user_memory with the history loaded.
    """
    await user_memory.clear()
    history = await get_chat_history(session_id)
    logger.info("reloading history")
    logger.info(history)

    if history["chat_history"]:
        for entry in history["chat_history"]:
            await user_memory.add(
                MemoryContent(
                    content=entry["user_proxy"], mime_type=MemoryMimeType.TEXT
                )
            )
            await user_memory.add(
                MemoryContent(
                    content=entry["assistant_agent"], mime_type=MemoryMimeType.TEXT
                )
            )
        if history["token"]:
            await user_memory.add(
                MemoryContent(
                    content=f"User has authenticated the session_id or has logged in.",
                    mime_type=MemoryMimeType.TEXT,
                )
            )

    return user_memory


async def make_request(
    session_id: Annotated[UUID, "The chat session ID"],
    url: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a request to the API with proper error handling.
    Args:
        url (str): The URL to make the request to.
        session_id (str): The session ID to use for authentication.
        method (str, optional): The HTTP method to use. Defaults to "GET".
        data (dict[str, Any], optional): The data to send with the request. Defaults to None.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    token = await get_token(session_id)

    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as client:
        try:
            if method == "POST":
                response = await client.post(
                    url, headers=headers, timeout=30.0, json=data
                )
            else:
                response = await client.get(url, headers=headers, timeout=30.0)

            if response.status_code == 401:
                return {"error": "Unauthorized request."}

            return response.json()
        except Exception:
            return {"error": "Unexpected error occurred. Please try again later."}


async def chat_stream(
    session_id: Annotated[UUID, "The chat session ID"],
    team: SelectorGroupChat,
    user_input: str,
    termination: ExternalTermination,
) -> AsyncGenerator[bytes, None]:

    stream = team.run_stream(task=user_input, cancellation_token=CancellationToken())

    try:

        async for message in stream:
            # skip internals
            if isinstance(
                message, (ToolCallExecutionEvent, MemoryQueryEvent, ThoughtEvent)
            ):
                continue
            message_type = "text"
            source = getattr(message, "source", "")
            content = getattr(message, "content", "")

            # if isinstance(message, UserInputRequestedEvent):
            #     logger.info("Caught UserInputRequestedEvent")
            #     termination.set()
            #     break

            if source == "":
                continue

            if source == "user" or source == "user_proxy" or source == "user_agent":
                await add_chat_entry(str(session_id), source, content, message_type)
                continue

            if "TERMINATE" in content:
                content = content.split("TERMINATE", 1)[0].strip()

            if isinstance(message, TextMessage):
                await add_chat_entry(str(session_id), source, content, message_type)

            if isinstance(message, ToolCallRequestEvent):
                tool_name = message.content[0].name
                content = f"Calling tool: {tool_name}"
                message_type = "tool_call"
                await add_chat_entry(str(session_id), source, content, message_type)

            if isinstance(
                message, (FunctionExecutionResultMessage, ToolCallSummaryMessage)
            ):

                item = (
                    message.content[0]
                    if isinstance(message.content, list)
                    else message.content
                )
                tool_name = getattr(item, "name", "Tool execution result")
                tool_result = getattr(item, "content", item)
                message_type = "tool_result"
                content = f"```py\n{tool_result}\n```"
                await add_chat_entry(str(session_id), source, content, message_type)

            yield encode_json(
                {"type": message_type, "role": source, "message": content}
            ) + b"\n"
            continue

        team_state = await team.save_state()
        chat_history = json.dumps(team_state, ensure_ascii=False, indent=2, default=str)
        await save_chat_history(session_id, chat_history)

    except Exception:

        err = {"role": "system", "message": " An error occurred—please try again."}
        yield (json.dumps(err) + "\n").encode("utf-8")


async def save_chat_history(
    session_id: Annotated[UUID, "The chat session ID"], team_state: str
) -> None:
    logger.info("saving data")
    async with aiofile.async_open(f"{team_dir_path}/{session_id}.json", "w") as f:
        await f.write(team_state)


def fetch_new_submissions(subreddit: Any, limit: int = 10) -> list:
    submissions = []
    for submission in subreddit.new(limit=limit):
        submissions.append(submission)
    return submissions


def fetch_hot_submissions(subreddit: Any, limit: int = 10) -> list:
    submissions = []
    for submission in subreddit.hot(limit=limit):
        submissions.append(submission)
    return submissions


async def authenticate_youtube():
    creds = None

    if os.path.exists(token_filepath):
        creds = Credentials.from_authorized_user_file(token_filepath, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                clinet_secrets_filepath, SCOPES
            )
            creds = flow.run_local_server(port=0)

        async with aiofile.async_open(token_filepath, "w") as f:
            await f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_video(
    youtube, file_path, title, description, tags, category_id, privacy_status
):
    body = dict(
        snippet=dict(
            title=title, description=description, tags=tags, categoryId=category_id
        ),
        status=dict(privacyStatus=privacy_status),
    )
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part=",".join(body.keys()), body=body, media_body=media
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")
    print(f"Upload complete! Video ID: {response['id']}")


async def save_download_history(
    session_id: Annotated[UUID, "The chat session ID"], history: list[dict[str, Any]]
) -> None:
    logger.info("saving data")
    async with aiofile.async_open(f"{download_dir_path}/{session_id}.json", "w") as f:
        await f.write(json.dumps(history, indent=2))


async def login(session_id: UUID, email: str, password: str) -> dict:
    """Login to the targeted API that u want to fetch data from and retrieve a token."""

    url = f"{API_BASE_URL}/auth/tokens"
    response = await make_request(
        url=url,
        session_id=session_id,
        method="POST",
        data={"email": email, "password": password, "type": "ANALYST"},
    )

    return response
