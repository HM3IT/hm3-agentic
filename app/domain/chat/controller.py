import warnings
from uuid import UUID

from typing import Annotated
from pathlib import Path
from structlog import getLogger
from pydantic import BaseModel

from litestar import Controller, get, post
from litestar.exceptions import HTTPException
from litestar.response import Stream

from app.config.base import get_settings
from app.domain.chat.utils import chat_stream, get_team_state, get_chat_history, login
from app.domain.chat.tools import (
    download_reddit_video,
    upload_to_youtube,
    save_token,
    generate_auth_url,
    verify_token,
)

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.teams import SelectorGroupChat
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.conditions import (
    TextMentionTermination,
    MaxMessageTermination,
    TimeoutTermination,
    ExternalTermination,
)

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
    email: str
    password: str


class ChatController(Controller):

    @post("/api/chats/authenticate")
    async def authenticate(self, data: ChatAuth, session_id: UUID) -> None:
        res = await login(session_id, data.email, data.password)
        if res.get("error"):
            raise HTTPException(status_code=401, detail="Failed to authenticate")
        await save_token(session_id, res["access_token"])

    @get("/api/chats/{session_id: str}")
    async def list_chats(self, session_id: UUID) -> list:
        """List all chats for a given session."""

        chat = await get_chat_history(session_id)
        return chat.get("chat_history", [])

    @post("/api/chats/{session_id: uuid}")
    async def query_chat(
        self, session_id: Annotated[UUID, "The chat session ID"], data: ChatUserInput
    ) -> Stream:
        """Chat with the HM3 API."""
        Path(chat_history_folder_path).mkdir(parents=True, exist_ok=True)
        user_input = data.message.strip()

        request_agent = AssistantAgent(
            name="hm3_social_media_agent",
            description="An agent that can interact with Reddit and YouTube.",
            model_client=model_client,
            tools=[
                download_reddit_video,
                upload_to_youtube,
            ],
            reflect_on_tool_use=False,
            system_message=f"""You are a social media agent for chat session ID: {session_id}. Use the tools available to you
            to get download reddit videos and upload them.
            Summarize the output from the tools to the user after each tool call.
            Use "TERMINATE" when the task is complete or when you want the user's input.
            """,
        )

        auth_agent = AssistantAgent(
            name="hm3_auth_agent",
            description="An agent that can generate an authentication URL for the user and verify user's access after authentication.",
            model_client=model_client,
            tools=[generate_auth_url, verify_token],
            reflect_on_tool_use=False,
            system_message=f"""
            You are an auth agent for chat session ID: {session_id}.
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
            return input(prompt or "Action required: ")

        user_agent = UserProxyAgent(
            "user_agent",
            description="A human user. This agent is to be selected if the assistant agents need human action to work",
            input_func=ask_input,
        )

        external_termination = ExternalTermination()

        hm3_team = SelectorGroupChat(
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

            Current conversation context:
            {history}

            Read the above conversation, then select the most appropriate agent from {participants} to route the message to.
            Only select one agent. Route the request back to the user if human action is required.
            For general conversation, select the hm3_general_agent.
            """,
            allow_repeated_speaker=True,
        )

        team_state = await get_team_state(session_id)
        if team_state:
            await hm3_team.load_state(team_state)

        return Stream(
            chat_stream(session_id, hm3_team, user_input, external_termination)
        )
