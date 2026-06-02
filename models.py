from pydantic import BaseModel


class MusicPickRequest(BaseModel):
    music_style_prompt: str


class MusicPickResponse(BaseModel):
    music_filename: str        # Local filename in MUSIC_DIR — pass to /produce-video or /process-video
    music_track_id: int | None = None
    reused: bool
    library_count: int


class VideoRequest(BaseModel):
    language: str              # "español" | "portugues" | "ingles"
    language_code: str         # "es" | "pt" | "en"
    voice_id: str
    text: str
    title: str
    avatar_id: str
    music_style_prompt: str
    music_filename: str | None = None  # From /pick-music; if None, generates internally


class VideoResponse(BaseModel):
    status: str
    language: str
    file_id: str               # Pass to GET /files/{file_id} to download
    download_url: str          # {BASE_URL}/files/{file_id}
    duration_seconds: int
    steps_completed: list[str]
    music_reused: bool = False


class ProcessVideoRequest(BaseModel):
    video_url: str             # Downloadable URL of the pastor-recorded video
    title: str
    language: str = "español"
    language_code: str = "es"
    music_filename: str | None = None  # From /pick-music; if None, generates internally
    music_style_prompt: str = "Uplifting Christian worship instrumental background music, peaceful and inspiring"


class ProcessVideoResponse(BaseModel):
    status: str
    file_id: str
    download_url: str
    duration_seconds: int
    steps_completed: list[str]


class ProductionCreate(BaseModel):
    """Body for POST /productions — called by n8n after Drive upload is confirmed."""
    language: str
    language_code: str
    title: str
    video_url: str             # Google Drive URL
    video_drive_file_id: str   # Drive file ID
    music_track_id: int | None = None
    music_reused: bool = False
    pipeline_seconds: int = 0
