"""
YouTube upload integration with OAuth authorization.

This module supports:
- Interactive Google OAuth login (opens browser)
- Persistent token storage
- Uploading videos as Shorts, regular videos, or auto mode
"""

import os
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.config import config
from app.utils import utils

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]
except ImportError:
    Request = None
    Credentials = None
    build = None
    MediaFileUpload = None
    InstalledAppFlow = None
    HttpError = Exception


class YouTubeUploadService:
    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]

    def __init__(self):
        self._reload_config()

    def _reload_config(self):
        self.enabled = bool(config.app.get("youtube_upload_enabled", False))
        self.auto_upload = bool(config.app.get("youtube_auto_upload", False))
        self.publish_mode = str(config.app.get("youtube_publish_mode", "auto")).strip().lower()
        self.privacy_status = str(config.app.get("youtube_privacy_status", "private")).strip().lower()
        self.category_id = str(config.app.get("youtube_category_id", "22")).strip() or "22"
        self.client_id = str(config.app.get("youtube_client_id", "")).strip()
        self.client_secret = str(config.app.get("youtube_client_secret", "")).strip()

        raw_tags = config.app.get("youtube_tags", ["0CodeAutoGen", "AIVideo"])
        self.default_tags = self._as_list(raw_tags)

        raw_client_file = str(config.app.get("youtube_client_secrets_file", "")).strip()
        self.client_secrets_file = self._resolve_path(raw_client_file)

        raw_token_file = str(config.app.get("youtube_token_file", "")).strip()
        if raw_token_file:
            self.token_file = self._resolve_path(raw_token_file)
        else:
            self.token_file = os.path.join(utils.storage_dir("oauth", create=True), "youtube_token.json")

        token_dir = os.path.dirname(self.token_file)
        if token_dir and not os.path.exists(token_dir):
            os.makedirs(token_dir, exist_ok=True)

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if isinstance(value, list):
            items = [str(v).strip() for v in value if str(v).strip()]
        elif isinstance(value, str):
            items = [x.strip() for x in value.split(",") if x.strip()]
        else:
            items = []

        deduped = []
        seen = set()
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _ensure_google_deps() -> bool:
        """Try importing Google SDKs at runtime so restart is not required after pip install."""
        global Request, Credentials, build, MediaFileUpload, InstalledAppFlow, HttpError

        if all([Request, Credentials, build, MediaFileUpload, InstalledAppFlow]):
            return True

        try:
            from google.auth.transport.requests import Request as _Request
            from google.oauth2.credentials import Credentials as _Credentials
            from googleapiclient.discovery import build as _build
            from googleapiclient.errors import HttpError as _HttpError
            from googleapiclient.http import MediaFileUpload as _MediaFileUpload
            from google_auth_oauthlib.flow import InstalledAppFlow as _InstalledAppFlow  # type: ignore[import-not-found]

            Request = _Request
            Credentials = _Credentials
            build = _build
            HttpError = _HttpError
            MediaFileUpload = _MediaFileUpload
            InstalledAppFlow = _InstalledAppFlow
            return True
        except Exception:
            return False

    @staticmethod
    def _resolve_path(path_value: str) -> str:
        if not path_value:
            return ""
        if os.path.isabs(path_value):
            return path_value
        return os.path.join(utils.root_dir(), path_value)

    def _is_deps_ready(self) -> bool:
        return self._ensure_google_deps()

    def _has_secret_file(self) -> bool:
        return bool(self.client_secrets_file) and os.path.isfile(self.client_secrets_file)

    def _has_inline_client_config(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _build_inline_client_config(self) -> Dict[str, Any]:
        return {
            "installed": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        mode = str(mode or "auto").strip().lower()
        if mode in {"short", "shorts"}:
            return "shorts"
        if mode in {"video", "long"}:
            return "video"
        return "auto"

    @staticmethod
    def _normalize_privacy(privacy: str) -> str:
        privacy = str(privacy or "private").strip().lower()
        if privacy in {"public", "private", "unlisted"}:
            return privacy
        return "private"

    def is_configured(self) -> bool:
        self._reload_config()
        return self.enabled and (self._has_secret_file() or self._has_inline_client_config())

    def should_auto_upload(self) -> bool:
        self._reload_config()
        return bool(self.enabled and self.auto_upload)

    def _load_credentials(self) -> Optional[Any]:
        if not self._is_deps_ready() or not self.token_file or not os.path.exists(self.token_file):
            return None
        try:
            return Credentials.from_authorized_user_file(self.token_file, self.SCOPES)
        except Exception as e:
            logger.warning(f"failed to load YouTube token file: {e}")
            return None

    def _save_credentials(self, creds: Any):
        if not creds:
            return
        with open(self.token_file, "w", encoding="utf-8") as fp:
            fp.write(creds.to_json())

    def auth_status(self) -> Dict[str, Any]:
        self._reload_config()

        if not self._is_deps_ready():
            return {
                "success": False,
                "authorized": False,
                "configured": False,
                "message": "Missing YouTube dependencies. Install google-api-python-client, google-auth-httplib2, google-auth-oauthlib.",
            }

        configured = self._has_secret_file() or self._has_inline_client_config()
        if not configured:
            return {
                "success": False,
                "authorized": False,
                "configured": False,
                "message": "Set either youtube_client_secrets_file OR youtube_client_id + youtube_client_secret.",
            }

        creds = self._load_credentials()
        authorized = bool(creds and creds.valid)
        return {
            "success": True,
            "authorized": authorized,
            "configured": True,
            "token_file": self.token_file,
            "client_secrets_file": self.client_secrets_file,
            "uses_inline_client_config": self._has_inline_client_config() and not self._has_secret_file(),
            "message": "YouTube is authorized." if authorized else "Authorization required. Click Authorize YouTube Account.",
        }

    def authorize(self, interactive: bool = False, force: bool = False) -> Dict[str, Any]:
        self._reload_config()

        if not self._is_deps_ready():
            return {
                "success": False,
                "authorized": False,
                "needs_authorization": True,
                "message": "YouTube dependencies are missing. Please install requirements.txt first.",
            }

        if not self.enabled:
            return {
                "success": False,
                "authorized": False,
                "needs_authorization": False,
                "message": "YouTube upload is disabled in config.",
            }

        if not (self._has_secret_file() or self._has_inline_client_config()):
            return {
                "success": False,
                "authorized": False,
                "needs_authorization": True,
                "message": "Set either youtube_client_secrets_file OR youtube_client_id + youtube_client_secret.",
            }

        creds = None if force else self._load_credentials()

        if creds and creds.valid:
            return {
                "success": True,
                "authorized": True,
                "message": "YouTube account already authorized.",
            }

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_credentials(creds)
                return {
                    "success": True,
                    "authorized": True,
                    "message": "YouTube authorization refreshed.",
                }
            except Exception as e:
                logger.warning(f"failed to refresh YouTube token: {e}")

        if not interactive:
            return {
                "success": False,
                "authorized": False,
                "needs_authorization": True,
                "message": "YouTube account is not authorized yet. Trigger OAuth authorization first.",
            }

        try:
            if self._has_secret_file():
                flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, self.SCOPES)
            else:
                flow = InstalledAppFlow.from_client_config(self._build_inline_client_config(), self.SCOPES)

            creds = flow.run_local_server(
                host="localhost",
                port=0,
                open_browser=True,
                authorization_prompt_message="Opening browser for Google authorization...",
                success_message="Authorization completed. You can close this tab and return to 0Code AutoGen.",
            )
            self._save_credentials(creds)
            return {
                "success": True,
                "authorized": True,
                "message": "YouTube authorization completed successfully.",
            }
        except Exception as e:
            logger.error(f"YouTube authorization failed: {e}")
            return {
                "success": False,
                "authorized": False,
                "needs_authorization": True,
                "message": f"YouTube authorization failed: {e}",
            }

    @staticmethod
    def _is_shorts_candidate(video_path: str) -> bool:
        try:
            from moviepy.video.io.VideoFileClip import VideoFileClip

            clip = VideoFileClip(video_path)
            duration = float(clip.duration or 0)
            width, height = clip.size
            clip.close()
            return duration <= 60 and height >= width
        except Exception as e:
            logger.warning(f"failed to inspect video for Shorts detection: {e}")
            return False

    @staticmethod
    def _ensure_shorts_hashtag(title: str, description: str) -> Tuple[str, str]:
        text = f"{title} {description}".lower()
        if "#shorts" in text:
            return title, description
        if len(title) <= 91:
            return f"{title} #Shorts", description
        if description:
            return title, f"{description}\n\n#Shorts"
        return title, "#Shorts"

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        publish_mode: Optional[str] = None,
        privacy_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._reload_config()

        if not os.path.exists(video_path):
            return {"success": False, "error": f"Video file not found: {video_path}"}

        auth_result = self.authorize(interactive=False)
        if not auth_result.get("success"):
            return {
                "success": False,
                "error": auth_result.get("message", "YouTube authorization required."),
                "needs_authorization": bool(auth_result.get("needs_authorization", True)),
            }

        creds = self._load_credentials()
        if not creds or not creds.valid:
            return {
                "success": False,
                "error": "YouTube authorization is invalid. Please authorize again.",
                "needs_authorization": True,
            }

        mode = self._normalize_mode(publish_mode or self.publish_mode)
        privacy = self._normalize_privacy(privacy_status or self.privacy_status)

        is_shorts = mode == "shorts" or (mode == "auto" and self._is_shorts_candidate(video_path))

        merged_tags = self._as_list(self.default_tags + (tags or []))
        final_title = (title or "0Code AutoGen Video").strip()[:100]
        final_description = (description or "").strip()[:5000]

        if is_shorts:
            final_title, final_description = self._ensure_shorts_hashtag(final_title, final_description)

        snippet = {
            "title": final_title,
            "description": final_description,
            "categoryId": self.category_id,
        }
        if merged_tags:
            snippet["tags"] = merged_tags

        body = {
            "snippet": snippet,
            "status": {
                "privacyStatus": privacy,
            },
        }

        try:
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            media_file = MediaFileUpload(video_path, chunksize=-1, resumable=True)

            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media_file,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"YouTube upload progress: {int(status.progress() * 100)}%")

            video_id = response.get("id")
            video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
            logger.info(f"YouTube upload success: {video_url}")

            return {
                "success": True,
                "video_id": video_id,
                "url": video_url,
                "is_shorts": is_shorts,
                "publish_mode": mode,
                "privacy_status": privacy,
            }
        except HttpError as e:
            logger.error(f"YouTube upload failed: {e}")
            return {"success": False, "error": f"YouTube API error: {e}"}
        except Exception as e:
            logger.error(f"YouTube upload failed: {e}")
            return {"success": False, "error": str(e)}


# Singleton
youtube_upload_service = YouTubeUploadService()


def authorize_youtube_account(interactive: bool = True, force: bool = False) -> Dict[str, Any]:
    return youtube_upload_service.authorize(interactive=interactive, force=force)


def upload_to_youtube(
    video_path: str,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    publish_mode: Optional[str] = None,
    privacy_status: Optional[str] = None,
) -> Dict[str, Any]:
    return youtube_upload_service.upload_video(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        publish_mode=publish_mode,
        privacy_status=privacy_status,
    )
