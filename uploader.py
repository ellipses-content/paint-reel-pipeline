import os
import json
import pickle
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

SERIES_NAME = "Cryptid Files"
PLAYLIST_CACHE = "playlist_id.txt"

def get_youtube_client():
    """Build authenticated YouTube client from env credentials."""
    creds_json = os.environ.get("YOUTUBE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("YOUTUBE_CREDENTIALS_JSON not set")

    creds_data = json.loads(creds_json)
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=creds_data.get("client_id"),
        client_secret=creds_data.get("client_secret"),
        scopes=SCOPES
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return build("youtube", "v3", credentials=creds)


def get_or_create_playlist(youtube) -> str:
    """Find the Cryptid Files playlist or create it. Cache the ID."""
    if Path(PLAYLIST_CACHE).exists():
        playlist_id = Path(PLAYLIST_CACHE).read_text().strip()
        print(f"  [playlist] using cached ID: {playlist_id}")
        return playlist_id

    # Search existing playlists
    response = youtube.playlists().list(
        part="snippet",
        mine=True,
        maxResults=50
    ).execute()

    for item in response.get("items", []):
        if item["snippet"]["title"] == SERIES_NAME:
            playlist_id = item["id"]
            Path(PLAYLIST_CACHE).write_text(playlist_id)
            print(f"  [playlist] found existing: {playlist_id}")
            return playlist_id

    # Create new playlist
    playlist = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": SERIES_NAME,
                "description": (
                    "A faceless horror series exploring cryptids, monsters, and unexplained phenomena. "
                    "Each episode dives into a different creature from folklore and eyewitness accounts.\n\n"
                    "#cryptid #horror #shorts #cryptidfiles"
                ),
                "defaultLanguage": "en"
            },
            "status": {"privacyStatus": "public"}
        }
    ).execute()

    playlist_id = playlist["id"]
    Path(PLAYLIST_CACHE).write_text(playlist_id)
    print(f"  [playlist] created new: {playlist_id}")
    return playlist_id


def add_to_playlist(youtube, video_id: str, playlist_id: str):
    """Add a video to the Cryptid Files playlist."""
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id
                }
            }
        }
    ).execute()
    print(f"  [playlist] added video {video_id} to {playlist_id}")


def upload_unlisted(video_path: str, topic: str) -> str:
    """Upload the video as UNLISTED for human review. Returns the video ID."""
    youtube = get_youtube_client()

    title = f"Cryptid Files: {topic} 👁️ #shorts"
    description = (
        f"The truth about the {topic}. A creature that has haunted folklore and "
        f"eyewitness reports for centuries...\n\n"
        f"Part of the Cryptid Files series — new episodes drop daily.\n\n"
        f"#cryptid #{topic.lower().replace(' ', '')} #horror #shorts #cryptidfiles "
        f"#scary #paranormal #folklore #monster #unknown"
    )
    tags = [
        topic, "cryptid", "horror", "scary", "paranormal",
        "folklore", "monster", "shorts", "cryptid files",
        "unexplained", "eyewitness", "creature"
    ]

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "22",  # People & Blogs
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en"
            },
            "status": {
                "privacyStatus": "unlisted",
                "selfDeclaredMadeForKids": False,
                "madeForKids": False
            }
        },
        media_body=media
    )

    print(f"  [upload] uploading '{title}' as unlisted...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  [upload] {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"  [upload] done (unlisted) → https://youtu.be/{video_id}")
    return video_id


def make_public(video_id: str):
    """Flip an unlisted video to public and add it to the Cryptid Files playlist."""
    youtube = get_youtube_client()

    youtube.videos().update(
        part="status",
        body={
            "id": video_id,
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
                "madeForKids": False
            }
        }
    ).execute()
    print(f"  [publish] video {video_id} set to public → https://youtube.com/shorts/{video_id}")

    playlist_id = get_or_create_playlist(youtube)
    add_to_playlist(youtube, video_id, playlist_id)


def delete_video(video_id: str):
    """Delete a video entirely (used on reject / timeout)."""
    youtube = get_youtube_client()
    youtube.videos().delete(id=video_id).execute()
    print(f"  [delete] video {video_id} deleted")


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "Skinwalker"
    video_path = sys.argv[2] if len(sys.argv) > 2 else f"output/{topic.lower().replace(' ', '_')}/final.mp4"
    video_id = upload_unlisted(video_path, topic)
    print(f"Uploaded unlisted: {video_id}")
