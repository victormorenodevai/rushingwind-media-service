import io
import json
import logging
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_service(credentials_json: str):
    info = json.loads(credentials_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_file(
    file_path: str,
    file_name: str,
    folder_id: str,
    credentials_json: str,
    mimetype: str = "video/mp4",
) -> tuple[str, str]:
    """Upload any file to Drive. Returns (file_id, public_download_url)."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File to upload missing: {file_path}")

    service = _get_service(credentials_json)

    file_metadata = {"name": file_name, "parents": [folder_id]}
    media = MediaFileUpload(file_path, mimetype=mimetype, resumable=True)

    uploaded = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id")
        .execute()
    )

    file_id = uploaded.get("id")

    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    logger.info("Uploaded %s to Drive folder %s → id=%s", file_name, folder_id, file_id)
    return file_id, url


def upload_video(
    file_path: str,
    file_name: str,
    folder_id: str,
    credentials_json: str,
) -> str:
    """Backward-compatible wrapper around upload_file for MP4 videos."""
    file_id, _ = upload_file(file_path, file_name, folder_id, credentials_json, mimetype="video/mp4")
    return file_id


def list_mp3s(folder_id: str, credentials_json: str) -> list[dict]:
    """Return list of {id, name} for all MP3s in folder_id."""
    service = _get_service(credentials_json)
    query = f"'{folder_id}' in parents and mimeType='audio/mpeg' and trashed=false"
    results = (
        service.files()
        .list(q=query, fields="files(id,name)", orderBy="name")
        .execute()
    )
    files = results.get("files", [])
    logger.info("Music library: %d songs in folder %s", len(files), folder_id)
    return files


def download_file(file_id: str, dest_path: str, credentials_json: str) -> None:
    """Download a Drive file by ID to dest_path."""
    service = _get_service(credentials_json)
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    logger.info("Downloaded Drive file %s → %s", file_id, dest_path)
