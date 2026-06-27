"""
One-time helper to mint YouTube OAuth credentials for the pipeline.

Usage:
    1. Set the OAuth client credentials from your Google Cloud "Desktop app"
       OAuth client in the environment:

           Windows (PowerShell):
               $env:YOUTUBE_CLIENT_ID="xxx.apps.googleusercontent.com"
               $env:YOUTUBE_CLIENT_SECRET="yyy"

           macOS/Linux (bash):
               export YOUTUBE_CLIENT_ID="xxx.apps.googleusercontent.com"
               export YOUTUBE_CLIENT_SECRET="yyy"

    2. Run:  python get_youtube_token.py
    3. A browser window opens — sign in as the channel's owner and approve.
    4. Copy the single-line JSON printed at the end and save it as the
       GitHub Actions secret YOUTUBE_CREDENTIALS_JSON.

The printed JSON matches exactly what uploader.get_youtube_client() expects:
token, refresh_token, token_uri, client_id, client_secret.
"""

import os
import sys
import json

from google_auth_oauthlib.flow import InstalledAppFlow

# Must match the scopes used by uploader.py
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def main():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print(
            "ERROR: set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET environment "
            "variables before running this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)

    # access_type=offline + prompt=consent guarantees we receive a refresh_token,
    # which the unattended GitHub Action needs to refresh the access token.
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message=(
            "Opening your browser to authorize the YouTube channel.\n"
            "If it does not open, visit this URL manually:\n{url}"
        ),
        success_message=(
            "Authorization complete. You can close this tab and return to the terminal."
        ),
    )

    credentials_json = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }

    if not creds.refresh_token:
        print(
            "\nWARNING: no refresh_token was returned. Revoke this app's access at "
            "https://myaccount.google.com/permissions and run again so Google "
            "re-prompts for consent.\n",
            file=sys.stderr,
        )

    print("\n" + "=" * 70)
    print("Save the following single line as the GitHub secret "
          "YOUTUBE_CREDENTIALS_JSON:")
    print("=" * 70 + "\n")
    print(json.dumps(credentials_json))
    print()


if __name__ == "__main__":
    main()
