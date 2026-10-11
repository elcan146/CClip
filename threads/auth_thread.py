#!/usr/bin/env python3
"""YouTube authentication thread for CClip."""

import os

from PyQt6.QtCore import QThread, pyqtSignal


class AuthThread(QThread):
    success = pyqtSignal()
    error = pyqtSignal(str)

    def run(self):
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request as GRequest
            from google_auth_oauthlib.flow import InstalledAppFlow

            SCOPES = [
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/yt-analytics.readonly"
            ]
            creds = None
            token_path = os.path.join(os.path.dirname(__file__), "..", "user_data", "token.json")
            secrets_path = os.path.join(os.path.dirname(__file__), "..", "user_data", "client_secrets.json")

            if os.path.exists(token_path):
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(GRequest())
                else:
                    if not os.path.exists(secrets_path):
                        self.error.emit("client_secrets.json not found in user_data/!")
                        return
                    flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
                    creds = flow.run_local_server(port=0)
                with open(token_path, "w") as tf:
                    tf.write(creds.to_json())
            self.success.emit()
        except Exception as e:
            self.error.emit(str(e))
