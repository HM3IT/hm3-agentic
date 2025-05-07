import os
import asyncpraw

import uuid
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
    save_download_history,
    upload_youtube_video,
)
from google_auth_oauthlib.flow import Flow
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound
from kokoro import KPipeline
from IPython.display import display, Audio
import soundfile as sf
import torch
import os

__all__ = [
    "generate_auth_url_youtube",
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
DOWNLOAD_FOLDER_PATH = chat.DOWNLOAD_FOLDER_PATH
SCOPES = chat.YOUTUBE_SCOPES
CLIENT_SECRETS = chat.CLIENT_SECRETS_FILEPATH
OAUTH_REDIRECT_URI = chat.OAUTH_REDIRECT_URI

# async def generate_auth_url(session_id: Annotated[UUID, "The chat session ID"]) -> str:
#     """Generate an authentication URL for the user."""
#     return f"{chat.CHAT_API_BASE}/api/chats/authenticate?session_id={session_id}"


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
    session_id: Annotated[UUID, "The chat session ID"],
    subreddit_name: str,
    algorithm: Literal["top", "new"],
    desired_keywords: list[str] = [],
    limit: int = 1,
) -> list[dict[str, Any]]:
    """Download videos from a subreddit.
    Args:
        session_id (str): The chat session ID.
        subreddit_name (str): The name of the subreddit to download videos from.
        algorithm (Literal["top", "new"]): The algorithm to use for downloading videos.
        desired_keywords (list[str], optional): The keywords to search for in the video title.
        limit (int, optional): The number of videos to download. Defaults to 1.
    """
    reddit = asyncpraw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_AGENT_NAME,
        username=REDDIT_USERNAME,
        password=REDDIT_PASSWORD,
    )

    folder_path = f"{DOWNLOAD_FOLDER_PATH}/{subreddit_name}"
    os.makedirs(folder_path, exist_ok=True)

    subreddit = await reddit.subreddit(subreddit_name)

    posts = subreddit.top(limit=5) if algorithm == "top" else subreddit.new(limit=5)

    downloaded = []
    async for submission in posts:
        if len(downloaded) >= limit:
            break

        title_lower = submission.title.lower()

        if desired_keywords and not any(
            kw.lower() in title_lower for kw in desired_keywords
        ):
            continue

        if not getattr(submission, "is_video", False):
            continue

        try:
            dl = Downloader(max_q=True, path=folder_path, filename=submission.title)
            dl.url = submission.url
            dl.download()
        except Exception as e:

            print(f"Skipping {submission.url!r}: {e}")
            continue

        downloaded.append(
            {
                "title": submission.title,
                "url": submission.url,
                "file_path": f"{folder_path}/{submission.title}.mp4",
                "description": submission.selftext,
                "category_id": "22",
            }
        )

    if downloaded:
        await save_download_history(session_id=session_id, history=downloaded)
        return downloaded
    await reddit.close()
    return [{"message": "No videos found", "status_code": 200}]


async def comment_on_youtube(
    video_id: str,
    comment_text: str,
) -> dict[str, Any]:
    """
    Adds a comment to a YouTube video.

    Args:
        session_id (UUID): The chat session ID.
        video_id (str): The ID of the YouTube video to comment on.
        comment_text (str): The text of the comment to add.
    """

    youtube = await authenticate_youtube()
    body = {
        "snippet": {
            "videoId": video_id,
            "topLevelComment": {"snippet": {"textOriginal": comment_text}},
        }
    }
    try:
        youtube.commentThreads().insert(part="snippet", body=body).execute()
        return {"message": "Comment posted", "status_code": 201}
    except Exception as e:
        return {"error": str(e), "status_code": 500}


async def get_video_details(
    session_id: Annotated[UUID, "The chat session ID"], video_id: str
) -> dict:
    """
    Retrieves the details of a YouTube video.

    Args:
        session_id (UUID): The chat session ID.
        video_id (str): The ID of the YouTube video.

    Returns:
        dict: A dictionary containing the video details.
    """

    try:
        text = await extract_transcript_youtube(video_id)
        return {"source": "transcript", "content": text}
    except NoTranscriptFound:
        pass

    youtube = await authenticate_youtube()
    resp = youtube.videos().list(part="snippet", id=video_id).execute()
    desc = resp["items"][0]["snippet"]["description"]
    return {"source": "description", "content": desc}


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


def generate_auth_url_youtube() -> dict[str, str]:
    """
    Starts the OAuth flow by generating an endpoint to auth URL.
    """
    logger.info("SCOPE HEHE")
    logger.info(SCOPES)
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS,
        scopes=SCOPES,
        redirect_uri=OAUTH_REDIRECT_URI,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    return {"oauth_url": auth_url}


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

    youtube = await authenticate_youtube()
    response = await upload_youtube_video(
        youtube,
        file_path=file_path,
        title=title,
        description=description,
        tags=["tag1", "tag2"],
        category_id=str(category_id),
        privacy_status="private",
    )

    if response["status_code"] != 201:
        return {"error": "Failed to upload video to YouTube", "status_code": 500}

    youtube_id = response["id"]

    return {
        "message": "Successfully uploaded video to YouTube",
        "id": youtube_id,
        "status_code": 201,
    }



async def extract_transcript_youtube(video_id: str) -> str:
    """
    Extracts the transcript of a YouTube video.

    Args:
        video_id (str): The ID of the YouTube video.

    Returns:
        str: The transcript of the video.
    """
    transcript = YouTubeTranscriptApi.get_transcript(video_id)
    text = " ".join(segment["text"] for segment in transcript)
    return text




async def text_to_speech(text: str) -> str:
    """
    Converts text to speech.

    Args:
        text (str): The text to convert.

    Returns:
        str: The file path that the audio is saved.
    """
    os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"

    # 🇺🇸 'a' => American English, 🇬🇧 'b' => British English
    pipeline = KPipeline(lang_code="a")


    text = """
    Yes, that MANY player is just 10 percent of the community. Even F.G.O player like myself didn't bother about that. It's just refreshing to see saber in different outfit
    """

    generator = pipeline(
        text,
        voice="af_heart",
        speed=1,
        split_pattern=r"\n+",
    )
    random_uuid = UUID(int=uuid.uuid4().int)
    saved_file_path = f"{random_uuid}.wav"

    for i, (gs, ps, audio) in enumerate(generator):
        print(i)  # i => index
        print(gs)  # gs => graphemes/text
        print(ps)  # ps => phonemes
        display(Audio(data=audio, rate=24000, autoplay=i == 0))
        sf.write(saved_file_path, audio, 24000)
        
    return saved_file_path