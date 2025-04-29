import os
import praw

from uuid import UUID
from structlog import get_logger
from redvid import Downloader
from typing import Any, Annotated, Literal
from app.config.base import get_settings
from .utils import (
    get_token,
    update_token,
    make_request,
    authenticate_youtube,
    fetch_hot_submissions,
    fetch_new_submissions,
    upload_video,
)

__all__ = [
    "generate_auth_url",
    "verify_token",
    "save_token",
    "download_reddit_video",
    "upload_to_youtube",
]

logger = get_logger()
chat = get_settings().chat
API_BASE_URL = chat.API_BASE_URL

SESSION_FILE = "sessions.json"


REDDIT_CLIENT_ID = chat.REDDIT_CLIENT_ID
REDDIT_CLIENT_SECRET = chat.REDDIT_CLIENT_SECRET
REDDIT_PASSWORD = chat.REDDIT_PASSWORD
REDDIT_USERNAME = chat.REDDIT_USERNAME
REDDIT_AGENT_NAME = chat.REDDIT_AGENT_NAME
SUBREDDIT_NAME = chat.SUBREDDIT_NAME


async def generate_auth_url(session_id: Annotated[UUID, "The chat session ID"]) -> str:
    """Generate an authentication URL for the user."""
    return f"{chat.CHAT_API_BASE}/api/chats/authenticate?session_id={session_id}"


async def verify_token(session_id: Annotated[UUID, "The chat session ID"]) -> str:
    """Verify that the token is valid."""
    token = await get_token(session_id)
    if token:
        return "Token authenticated"
    return "Token not authenticated"


async def save_token(
    session_id: Annotated[UUID, "The chat session ID"],
    token: Annotated[str, "JWT access token to be saved"],
) -> str:
    """Save a user provided access token to a file."""
    await update_token(str(session_id), token)
    return "Token saved"


async def download_reddit_video(
    subreddit_name: str, algorithm: Literal["top", "new"], limit: int = 1
) -> list[dict[str, Any]]:
    """Download videos from a subreddit.
    Args:
        subreddit_name (str): The name of the subreddit to download videos from.
        algorithm (Literal["top", "new"]): The algorithm to use for downloading videos.
        limit (int, optional): The number of videos to download. Defaults to 1.

    """
    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_AGENT_NAME,
        username=REDDIT_USERNAME,
        password=REDDIT_PASSWORD,
    )

    path = f"./downloads/{subreddit_name}"

    os.makedirs(path, exist_ok=True)
    subreddit = reddit.subreddit(SUBREDDIT_NAME)
    if algorithm == "top":
        subbmissions = fetch_hot_submissions(subreddit, limit=limit)
    elif algorithm == "new":
        subbmissions = fetch_new_submissions(subreddit, limit=limit)
    else:
        raise ValueError("Invalid algorithm")

    downloaded_file = []
    for submission in subbmissions:

        reddit = Downloader(max_q=True, path=path)
        reddit.url = submission.url
        reddit.download()

        downloaded_file.append(
            {
                "title": submission.title,
                "url": submission.url,
                "filepath": path,
                "description": submission.selftext,
                "category_id": "22",
            }
        )

    return downloaded_file


async def get_youtube_categories(
    session_id: Annotated[UUID, "The chat session ID"], region_code: str = "US"
) -> list[dict[str, str]]:
    """
    Retrieves the list of YouTube video categories for a specified region.

    Args:
        session_id (UUID): The chat session ID.
        region_code (str): The region code (default is 'US').

    Returns:
        list[dict[str, str]]: A list of dictionaries containing category IDs and titles.
    """

    # TODO retrieved YouTube Data API v3 key.
    api_key = ""
    url = "https://www.googleapis.com/youtube/v3/videoCategories"
    params = {"part": "snippet", "regionCode": region_code, "key": api_key}

    response = await make_request(
        session_id=session_id, url=url, data=params, method="GET"
    )

    categories = []
    for item in response.get("items", []):
        categories.append({"id": item["id"], "title": item["snippet"]["title"]})

    return categories


async def upload_to_youtube(
    title: str, description: str, file_path: str, category_id: int
) -> dict[str, str | int]:
    """Upload a video to YouTube.
    Args:
        title (str): The title of the video.
        description (str): The description of the video.
        file_path (str): The path to the video file.
        category_id (int): The category ID of the YouTube video.
    """
    try:
        youtube = authenticate_youtube()
        upload_video(
            youtube,
            file_path=file_path,
            title=title,
            description=description,
            tags=["tag1", "tag2"],
            category_id=str(category_id),
            privacy_status="private",
        )
        return {"message": "Successfully uploaded video to YouTube", "status_code": 201}
    except Exception as e:
        return {"error": "Failed to upload video to YouTube", "status_code": 500}
