import os
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


token_filepath="token.json"
clinet_secrets_filepath="client_secrets.json"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

 
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def authenticate_youtube():
    creds = None
 
    print(f"Looking for token at {token_filepath!r}")
    if os.path.exists(token_filepath):
        try:
            creds = Credentials.from_authorized_user_file(token_filepath, SCOPES)
            print("Loaded stored credentials")
        except Exception as e:
            print(f"Failed to load stored credentials: {e!r}")
            creds = None

    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("Refreshed access token")
            except RefreshError:
                print("Refresh token invalid; deleting and re-authorizing")
                os.remove(token_filepath)
                creds = None

        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                clinet_secrets_filepath, SCOPES
            )
            creds = flow.run_local_server(port=0)
            print("Completed new OAuth flow")
 
        with open(token_filepath, "w") as token:
            token.write(creds.to_json())
            print(f"Saved new token to {token_filepath!r}")

 
    service = build("youtube", "v3", credentials=creds)
    print("YouTube client ready")
    return service

def upload_video(youtube, file_path, title, description, tags, category_id, privacy_status):
    body=dict(
        snippet=dict(
            title=title,
            description=description,
            tags=tags,
            categoryId=category_id
        ),
        status=dict(
            privacyStatus=privacy_status
        )
    )
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")
    print(f"Upload complete! Video ID: {response['id']}")
    
    
    
 
youtube = authenticate_youtube()
upload_video(
    youtube,
    file_path="../download_history/WutheringWavesLeaks/Zani gameplay.mp4",
    title="Zani gameplay",
    description="Gameplay footage of Zani from Wuthering Waves. This video was originally shared on the WutheringWavesLeaks subreddit.",
    tags=['tag1', 'tag2'],
    category_id='22',   
    privacy_status='private'  
)