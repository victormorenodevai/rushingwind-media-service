from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ELEVENLABS_API_KEY: str
    HEYGEN_API_KEY: str
    KIE_API_KEY: str
    GDRIVE_CREDENTIALS_JSON: str   # Full service account JSON as a string

    GDRIVE_MUSIC_LIBRARY_FOLDER_ID: str = ""  # Drive folder for music library (empty = always generate)
    MUSIC_LIBRARY_SIZE: int = 15              # Max songs before reusing instead of generating

    DATABASE_URL: str = ""                    # Injected by Railway Postgres plugin

    KIE_CALLBACK_URL: str = ""          # Set to https://your-media-service.up.railway.app/music-callback

    HEYGEN_POLL_TIMEOUT: int = 1500   # seconds (25 min)
    KIE_POLL_TIMEOUT: int = 600       # seconds (10 min)
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
