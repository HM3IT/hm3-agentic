import os
import json
import httpx
import aiofile
import aioboto3

from uuid import UUID
from structlog import getLogger

from sqlalchemy import select
from sqlalchemy.orm import load_only

from typing import Any, Annotated, Literal
from structlog import getLogger
from datetime import datetime, timezone

from collections.abc import AsyncGenerator

from autogen_core import CancellationToken
from autogen_core.models import FunctionExecutionResultMessage
from autogen_core.memory import ListMemory, MemoryContent, MemoryMimeType

from autogen_agentchat.teams import SelectorGroupChat
from autogen_agentchat.messages import (
    UserInputRequestedEvent,
    ToolCallExecutionEvent,
    ToolCallRequestEvent,
    MemoryQueryEvent,
    ThoughtEvent,
    TextMessage,
    ToolCallSummaryMessage,
)
from autogen_agentchat.conditions import ExternalTermination

from litestar.serialization import encode_json
from litestar.exceptions import NotFoundException
from litestar.di import Provide

from app.config.base import get_settings
from app.domain.chat.service import ChatMessageService
from app.domain.chat.deps import provide_chat_service
from app.db import models as m

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


__all__ = [
    "delete_team_state",
    "upload_youtube_video",
    "authenticate_youtube",
]

logger = getLogger()
chat = get_settings().chat


SCOPES = chat.YOUTUBE_SCOPES

API_BASE_URL = chat.API_BASE_URL
USER_AGENT = chat.USER_AGENT_NAME
session_file_path = chat.CHAT_HISTORY_FOLDER_PATH + "/sessions.json"
team_dir_path = chat.CHAT_HISTORY_FOLDER_PATH
download_dir_path = chat.DOWNLOAD_FOLDER_PATH
token_filepath = chat.TOKEN_FILEPATH
clinet_secrets_filepath = chat.CLIENT_SECRETS_FILEPATH

team_folder_path = os.environ["CHAT_HISTORY_FILE_PATH"] + "teams"

llm_name = os.environ["MODEL_NAME"]


async def generate_title(input_text: str) -> str:

    TOPIC_SYSTEM_PROMPT = """
    You are a topic generator. Your job is to create a concise title based on the conversation between the user and the FOAI (Free Open Access Information) Agents.
    The title must be no more than 5 words and output only the topic as a plain string.
    Examples:
    - FOAI New Request
    - Missing Document Follow-Up
    - FOAI Authorization Step 
    """

    user_message = f"Generate a brief topic that best summarizes the following conversation:\n\n{input_text}"

    messages = [
        {
            "role": "user",
            "content": [{"text": user_message}],
        }
    ]

    system_prompts = [{"text": TOPIC_SYSTEM_PROMPT}]

    boto_session = aioboto3.Session()
    client = boto_session.client(
        service_name="bedrock-runtime",
        region_name=os.environ["AWS_REGION"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
    response = await client.converse(
        modelId=llm_name,
        messages=messages,
        system=system_prompts,
        inferenceConfig={
            "maxTokens": 2024,
            "temperature": 0.2,
            "topP": 0.9,
        },
    )

    topic = response["output"]["message"]["content"][0]["text"]
    return topic if topic else "Unknown Topic"



async def async_read_json(file_path: str) -> dict[str, Any]:
    if os.path.exists(file_path):
        async with aiofile.async_open(file_path, "r") as f:
            content = await f.read()
            if content:
                return json.loads(content)
    return {}
 

async def get_team_state(
    chat_id: str,
) -> dict[str, Any] | None:

    state_file_path = f"{team_folder_path}/{chat_id}.json"
    if os.path.exists(state_file_path):
        return await async_read_json(state_file_path)
    return None


async def load_preference_memory(
    chat_id: Annotated[UUID, "The chat session ID"], user_memory: "ListMemory"
) -> "ListMemory":
    """
    Reloads the chat history for a given chat_id from a JSON file, and populates
    the user_memory with the history.

    Args:
        chat_id (str): the chat_id to load the history for.
        user_memory (ListMemory): the memory to populate with the history.

    Returns:
        ListMemory: the user_memory with the history loaded.
    """
    # TODO refactor
    await user_memory.clear()
    history = {}

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
                    content=f"User has authenticated the chat_id or has logged in.",
                    mime_type=MemoryMimeType.TEXT,
                )
            )

    return user_memory


async def get_token(chat_id: str):
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.config.app import alchemy  
    
    get_user_token_query = select(m.Chat).where(m.Chat.id == chat_id).options(load_only(m.Chat.token))
    
    async with alchemy.get_session() as db_session:
        result = await db_session.execute(get_user_token_query)
        token = result.scalar_one_or_none()
        return token


async def make_request(
    chat_id: Annotated[UUID, "The chat session ID"],
    url: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a request to the API with proper error handling.
    Args:
        url (str): The URL to make the request to.
        chat_id (str): The session ID to use for authentication.
        method (str, optional): The HTTP method to use. Defaults to "GET".
        data (dict[str, Any], optional): The data to send with the request. Defaults to None.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = await get_token(str(chat_id))
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
    chat_id: str,
    team: SelectorGroupChat,
    user_input: str,
    termination: ExternalTermination,
    chat_message_service: ChatMessageService,
) -> AsyncGenerator[bytes, None]:

    stream = team.run_stream(task=user_input, cancellation_token=CancellationToken())

    try:

        async for message in stream:
            # skip internals
            if isinstance(
                message, (ToolCallExecutionEvent, MemoryQueryEvent, ThoughtEvent)
            ):
                continue

            message_type: Literal["text", "tool_call", "tool_result"] = "text"
            source = getattr(message, "source", "")
            content = getattr(message, "content", "")

            if isinstance(message, UserInputRequestedEvent):
                logger.info("Caught UserInputRequestedEvent")
                termination.set()
                break

            if source == "":
                continue

            if source == "user" or source == "user_proxy" or source == "user_agent":
                await chat_message_service.create(
                    data={
                        "role": source,
                        "content": content,
                        "type": message_type,
                    }
                )
                continue

            if "TERMINATE" in content:
                content = content.split("TERMINATE", 1)[0].strip()

            if isinstance(message, TextMessage):
                await chat_message_service.create(
                    data={
                        "role": source,
                        "content": content,
                        "type": message_type,
                    }
                )

            if isinstance(message, ToolCallRequestEvent):
                tool_name = message.content[0].name
                content = f"Calling tool: {tool_name}"
                message_type = "tool_call"
                await chat_message_service.create(
                    data={
                        "role": source,
                        "content": content,
                        "type": message_type,
                    }
                )

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
                content = f"```json\n{tool_result}\n```"
                await chat_message_service.create(
                    data={
                        "role": source,
                        "content": content,
                        "type": message_type,
                    }
                )

            yield encode_json(
                {"type": message_type, "role": source, "message": content}
            ) + b"\n"
            continue

        team_state = await team.save_state()

        await save_team_state(
            chat_id,
            json.dumps(team_state, ensure_ascii=False, indent=2, default=str),
        )

    except Exception as e:
        logger.info(e)

        err = {"role": "system", "message": " An error occurred—please try again."}
        yield (json.dumps(err) + "\n").encode("utf-8")


async def save_team_state(chat_id: str, team_state: str) -> None:
    logger.info("Saving team state: " + chat_id)
    async with aiofile.async_open(f"{team_folder_path}/{chat_id}.json", "w") as f:
        await f.write(team_state)


def delete_team_state(chat_id: str) -> None:
    delete_team_file_path = f"{team_folder_path}/{chat_id}.json"

    if os.path.exists(delete_team_file_path):
        logger.info("Found the state file")
        try:
            os.remove(delete_team_file_path)
            logger.info("State file deleted successfully.")
        except OSError as e:
            logger.error(f"Failed to delete state file: {e}")
    else:
        logger.info("State file not found.")


async def login(chat_id: UUID, email: str, password: str) -> dict:
    """Login to the FOIAKit API and retrieve a token."""

    url = f"{API_BASE_URL}/auth/login"
    response = await make_request(
        url=url,
        chat_id=chat_id,
        method="POST",
        data={"email": email, "password": password, "type": "ANALYST"},
    )

    return response


async def authenticate_youtube():
    creds = None

    if os.path.exists(token_filepath):
        try:
            creds = Credentials.from_authorized_user_file(token_filepath, SCOPES)
            logger.info("Loaded stored credentials")
        except Exception as e:
            logger.info(f"Failed to load stored credentials: {e!r}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("Refreshed access token")
            except RefreshError:
                logger.info("Refresh token invalid; deleting and re-authorizing")
                os.remove(token_filepath)
                creds = None

        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                clinet_secrets_filepath, SCOPES
            )
            creds = flow.run_local_server(port=0)
            logger.info("Completed new OAuth flow")

        async with aiofile.async_open(token_filepath, "w") as token:
            await token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


async def upload_youtube_video(
    youtube, file_path, title, description, tags, category_id, privacy_status
) -> dict[str, Any]:
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

    return {
        "message": f"Upload complete! Video ID: {response['id']}",
        "status_code": 201,
        "id": response["id"],
    }


async def save_download_history(
    chat_id: Annotated[UUID, "The chat session ID"], history: list[dict[str, Any]]
) -> None:
    logger.info("saving downloaded history")
    async with aiofile.async_open(f"{download_dir_path}/{chat_id}.json", "w") as f:
        await f.write(json.dumps(history, indent=2))
