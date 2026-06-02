from pydantic import BaseModel


class MusicPickRequest(BaseModel):
    music_style_prompt: str   # English description, used only when generating a new song


class MusicPickResponse(BaseModel):
    music_drive_url: str      # Direct download URL from Drive — pass to /produce-video
    music_drive_file_id: str
    reused: bool
    library_count: int


class VideoRequest(BaseModel):
    language: str                        # "español" | "portugues" | "ingles"
    language_code: str                   # "es" | "pt" | "en"
    voice_id: str                        # ElevenLabs voice ID
    text: str                            # Script with optional [emotion] tags
    title: str                           # Used as the Drive filename
    avatar_id: str                       # HeyGen avatar ID
    drive_folder_id: str                 # Google Drive folder ID for final upload
    music_style_prompt: str              # English description for KIE music generation
    music_drive_url: str | None = None   # If set, skip KIE — download this URL instead


class VideoResponse(BaseModel):
    status: str
    language: str
    video_url: str
    drive_file_id: str
    duration_seconds: int
    steps_completed: list[str]
    music_reused: bool = False
    music_library_count: int = 0
