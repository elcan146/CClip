#!/usr/bin/env python3
"""YouTube upload functions for CClip."""

import os
import time

from config import S, update_s, log, yt_log


def run_youtube_upload(finish_cb):
    try:
        from googleapiclient.discovery import build as yt_build
        from googleapiclient.http import MediaFileUpload
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request as GRequest
        from google.oauth2.credentials import Credentials
    except ImportError as e:
        msg = f"Google API lib missing: {e}"
        log(msg, "error")
        yt_log(msg)
        finish_cb(False)
        return

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = None

    user_data_dir = os.path.join(os.path.dirname(__file__), "..", "user_data")
    token_path = os.path.join(user_data_dir, "token.json")
    secrets_path = os.path.join(user_data_dir, "client_secrets.json")

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GRequest())
        else:
            if not os.path.exists(secrets_path):
                yt_log("client_secrets.json not found!")
                finish_cb(False)
                return

            yt_log("Opening browser for YouTube OAuth...")
            flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as tf:
            tf.write(creds.to_json())

    update_s("yt_authed", True, trigger_save_popup=False)

    youtube = yt_build("youtube", "v3", credentials=creds)
    privacy = "public" if S["pub_mode"] else "private"
    uploaded_count = 0

    for movie_dir in os.listdir(S["out_folder"]):
        full_dir = os.path.join(S["out_folder"], movie_dir)

        if not os.path.isdir(full_dir):
            continue

        for fname in os.listdir(full_dir):
            if not fname.endswith(".mp4"):
                continue

            clip_name = os.path.splitext(fname)[0]
            video_path = os.path.join(full_dir, fname)
            meta_path = os.path.join(full_dir, f"{clip_name}_metadata.txt")

            title = "Awesome YouTube Short"
            desc = "#shorts #video"

            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as mf:
                    content = mf.read()

                if "TITLE:" in content and "DESCRIPTION:" in content:
                    parts = content.split("DESCRIPTION:")
                    if parts[0].replace("TITLE:", "").strip():
                        title = parts[0].replace("TITLE:", "").strip()[:100]
                    if parts[1].strip():
                        desc = parts[1].strip()

            yt_log(f"\nUploading: {title[:60]}... (Mode: {privacy.upper()})")

            body = {
                "snippet": {
                    "title": title,
                    "description": desc,
                    "categoryId": "24"
                },
                "status": {
                    "privacyStatus": privacy,
                    "selfDeclaredMadeForKids": False
                }
            }

            try:
                request = youtube.videos().insert(
                    part=",".join(body.keys()),
                    body=body,
                    media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True)
                )
                response = request.execute()

                yt_log(f"Uploaded! https://youtu.be/{response['id']}")
                uploaded_count += 1
                time.sleep(5)

            except Exception as e:
                yt_log(f"Upload failed: {e}")
                if "quotaExceeded" in str(e):
                    yt_log("Daily 10,000 quota exceeded. Try tomorrow!")

    yt_log(f"\nUpload session complete -- {uploaded_count} video(s) uploaded.")
    finish_cb(True)
