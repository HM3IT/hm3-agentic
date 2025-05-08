import aiofile
import warnings
from uuid import UUID

from typing import Annotated
from pathlib import Path
from structlog import getLogger
from pydantic import BaseModel
from google_auth_oauthlib.flow import Flow
from litestar import Controller, get, post
from litestar.exceptions import HTTPException, NotAuthorizedException, NotFoundException
from litestar.response import Stream, Response
from litestar.di import Provide
from litestar.background_tasks import BackgroundTask

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.teams import SelectorGroupChat
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.conditions import (
    TextMentionTermination,
    MaxMessageTermination,
    TimeoutTermination,
    ExternalTermination,
)

from app.config.base import get_settings
from app.domain.chat.utils import (
    chat_stream,
    get_team_state,
    delete_team_state,
    login,
    generate_title,
)
from app.domain.chat.tools import (
    download_reddit_video,
    upload_to_youtube,
    save_token,
    generate_auth_url_youtube,
    verify_token,
    comment_on_youtube,
)
from app.domain.chat.deps import provide_chat_service, provide_chat_message_service
from app.domain.chat.service import ChatService, ChatMessageService
from app.domain.chat.schemas import Chat, ChatMessage
from app.db import models as m

chat = get_settings().chat
logger = getLogger()

warnings.filterwarnings(
    "ignore", message="Claude models may not work with reflection on tool use*"
)
warnings.filterwarnings("ignore", message="Resolved model mismatch: Claude-3.5-Sonnet*")


model_name = chat.MODEL_NAME
model_family = chat.MODEL_FAMILY
api_key = chat.MODEL_API_KEY
base_url = chat.MODEL_BASE_URL
chat_history_folder_path = chat.CHAT_HISTORY_FOLDER_PATH
CLIENT_SECRETS = chat.CLIENT_SECRETS_FILEPATH
SCOPES = chat.YOUTUBE_SCOPES
TOKEN = chat.TOKEN_FILEPATH
OAUTH_REDIRECT_URI = chat.OAUTH_REDIRECT_URI

model_client = OpenAIChatCompletionClient(
    model=model_name,
    api_key=api_key,
    base_url=base_url,
    temperature=0.1,
    model_info={
        "vision": False,
        "function_calling": True,
        "json_output": True,
        "family": model_family,
        "structured_output": True,
        "multiple_system_messages": True,
    },
)


class ChatUserInput(BaseModel):
    message: str


class ChatAuth(BaseModel):
    username: str
    password: str


class ChatController(Controller):
    path = "/api/chats"
    tags = ["Chats"]

    dependencies = {
        "chat_service": Provide(provide_chat_service),
        "chat_message_service": Provide(provide_chat_message_service),
    }

    @post("/authenticate")
    async def authenticate(self, data: ChatAuth, session_id: UUID) -> None:
        res = await login(session_id, data.username, data.password)
        if not res.get("access_token"):
            raise HTTPException(status_code=401, detail="Failed to authenticate")
        await save_token(session_id, res["access_token"])

    @get("/create")
    async def create_chat(
        self,
        current_user: m.User,
        chat_service: ChatService,
    ) -> Chat:
        """List all chats of the user."""
        user_id = current_user.id
        chat_obj = await chat_service.create(
            data={"user_id": user_id, "title": "New Chat"}
        )

        return chat_service.to_schema(chat_obj, schema_type=Chat)

    @get("")
    async def list_chats(
        self,
        current_user: m.User,
        chat_service: ChatService,
    ) -> list[Chat]:
        """List all chats of the user."""
        user_id = current_user.id
        chat_objs, total = await chat_service.list_and_count(user_id=user_id)

        return [
            chat_service.to_schema(chat_obj, schema_type=Chat) for chat_obj in chat_objs
        ]

    @get("/{chat_id: uuid}/list")
    async def list_chat_messages(
        self,
        chat_id: Annotated[UUID, "The chat ID"],
        chat_message_service: ChatMessageService,
        current_user: m.User,
    ) -> list:
        """List all chats for a given session."""
        user_id = current_user.id
        chat_messages, count = await chat_message_service.list_and_count(
            user_id=user_id, chat_id=chat_id
        )

        return [
            chat_message_service.to_schema(chat_message, schema_type=ChatMessage)
            for chat_message in chat_messages
        ]

    @get("/{chat_id: uuid}")
    async def get_user_chat_detail(
        self,
        chat_id: Annotated[UUID, "The chat session ID"],
        chat_service: ChatService,
        current_user: m.User,
    ) -> Chat:
        """Get detailed user chat detail for a given session."""
        user_id = current_user.id

        chat = await chat_service.get_one_or_none(id=chat_id, user_id=user_id)

        if chat is None:
            raise NotFoundException(
                detail="User Chat History not found", status_code=404
            )

        return chat_service.to_schema(chat, schema_type=Chat)

    @post("/{chat_id: uuid}/delete")
    async def delete_chat(
        self,
        chat_id: Annotated[UUID, "The chat session ID"],
        chat_service: ChatService,
        current_user: m.User,
    ) -> Response:
        """Delete the given session of the user."""
        user_id = current_user.id

        if not await chat_service.get_one_or_none(id=chat_id, user_id=user_id):
            raise NotAuthorizedException(
                detail="User does not authorize to delete the chat of others",
                status_code=404,
            )

        await chat_service.delete(item_id=chat_id)

        chat_id_str = str(chat_id)
        # Delete the team state by off-loading with background task
        return Response(
            content={"chat_id": chat_id_str},
            status_code=200,
            background=BackgroundTask(delete_team_state, chat_id_str),
        )

    @get("/api/chats/{chat_id: uuid}/title/generate")
    async def generate_chat_title(
        self,
        chat_id: Annotated[UUID, "The chat session ID"],
        chat_message_service: ChatMessageService,
        chat_service: ChatService,
        current_user: m.User,
    ) -> Response:
        chat_messages, count = await chat_message_service.list_and_count(
            chat_id=chat_id
        )

        if count < 2:
            raise HTTPException(
                detail="Not enough messages to generate title", status_code=400
            )

        title_input_text = ""

        for chat_mesg in chat_messages[:2]:
            title_input_text += f"{chat_mesg.role}: {chat_mesg.content}\n"

        title = await generate_title(title_input_text)
        _ = await chat_service.update_title(chat_id=chat_id, title=title)

        return Response(content={"title": title}, status_code=201)

    @post("/{chat_id: uuid}/query")
    async def query_chat(
        self,
        chat_id: Annotated[UUID, "The chat ID"],
        data: ChatUserInput,
        current_user: m.User,
        chat_message_service: ChatMessageService,
    ) -> Stream:
        """Chat with the HM3 API."""
        Path(chat_history_folder_path).mkdir(parents=True, exist_ok=True)
        user_input = data.message.strip()
        user_id = current_user.id

        request_agent = AssistantAgent(
            name="hm3_social_media_agent",
            description="An agent that can interact with Reddit and YouTube.",
            model_client=model_client,
            tools=[
                download_reddit_video,
                upload_to_youtube,
                comment_on_youtube,
            ],
            reflect_on_tool_use=False,
            system_message=f"""You are a social media agent for chat ID: {chat_id}. Use the tools available to you
            to get download reddit videos and upload them. You can also comment on YouTube videos.
            Summarize the output from the tools to the user after each tool call.
            Use "TERMINATE" when the task is complete or when you want the user's input.
            """,
        )

        auth_agent = AssistantAgent(
            name="hm3_auth_agent",
            description="An agent that can generate an authentication URL for the user and verify user's access after authentication.",
            model_client=model_client,
            tools=[generate_auth_url_youtube, verify_token],
            reflect_on_tool_use=False,
            system_message=f"""
            You are an auth agent for chat ID: {chat_id}.
            Your job is to generate an authentication URL for the user if authentication is required.
            Never create a URL on your own. Always use the generate_auth_url tool to generate the auth URL.
            Send the authentication URL to the user in markdown format.
            """,
        )

        general_agent = AssistantAgent(
            name="hm3_general_agent",
            description="An agent to help with general knowledge about the process and general conversation. Useful for answering general questions.",
            model_client=model_client,
            reflect_on_tool_use=False,
            system_message=f"""
            You are a general knowledge agent. Your job is to answer general questions based on your knowledge.
            You are also capable of general conversation with the user.
            Use "TERMINATE" after each response.
            """,
        )

        def ask_input(prompt: str):
            logger.info("Skipping user input in non-interactive mode.")
            return "[NO INPUT]"

        user_agent = UserProxyAgent(
            "user_agent",
            description="A human user. This agent is to be selected if the assistant agents need human action to work",
            input_func=ask_input,
        )

        external_termination = ExternalTermination()

        team = SelectorGroupChat(
            model_client=model_client,
            participants=[request_agent, auth_agent, user_agent, general_agent],
            termination_condition=external_termination
            | TextMentionTermination("TERMINATE")
            | MaxMessageTermination(25)
            | TimeoutTermination(180),
            selector_prompt="""Select an agent to perform task.

            {roles}

            The following are the IMPORTANT rules that you must always follow:
            - After hm3_auth_agent an auth URL, always select the hm3_auth_agent again to send a message to the user before selecting the user_agent.
            - Only select the hm3_general_agent if the last message was from the user.
            - After a successful tool call, before selecting the next agent, always reselect its parent agent to let it send a message to the user about the  result.
            - Task related with reddit, Youtube, and any other social media platforms, always select the hm3_social_media_agent.

            Current conversation context:
            {history}

            Read the above conversation, then select the most appropriate agent from {participants} to route the message to.
            Only select one agent. Route the request back to the user if human action is required.
            For general conversation, select the hm3_general_agent.
            """,
            allow_repeated_speaker=True,
        )
        chat_id_str = str(chat_id)
        team_state = await get_team_state(chat_id_str)
        await chat_message_service.create(
            data={"chat_id": chat_id_str, "content": user_input, "role": "user"}
        )

        if team_state:
            await team.load_state(team_state)

        return Stream(chat_stream(chat_id_str, team, user_input, external_termination))
