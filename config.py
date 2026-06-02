from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ELEVENLABS_API_KEY: str = ""
    HEYGEN_API_KEY: str = ""
    KIE_API_KEY: str = ""

    MUSIC_LIBRARY_SIZE: int = 15      # Max songs before reusing instead of generating
    MUSIC_DIR: str = "/app/data/music"     # Persistent local music library
    STORAGE_DIR: str = "/app/data/storage" # Temp dir for produced videos (n8n downloads from here)
    BASE_URL: str = ""                # Public URL of this service, e.g. https://your-service.up.railway.app

    DATABASE_URL: str = ""            # Injected by Railway Postgres plugin

    LOGO_PATH: str = "/app/assets/rw-logo.png"

    HEYGEN_POLL_TIMEOUT: int = 1500   # seconds (25 min)
    KIE_POLL_TIMEOUT: int = 600       # seconds (10 min)
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
