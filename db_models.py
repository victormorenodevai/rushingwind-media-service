from datetime import datetime

from sqlmodel import Field, SQLModel


class MusicTrack(SQLModel, table=True):
    __tablename__ = "music_tracks"

    id: int | None = Field(default=None, primary_key=True)
    filename: str = Field(unique=True, index=True)  # local filename in MUSIC_DIR
    times_used: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Production(SQLModel, table=True):
    __tablename__ = "productions"

    id: int | None = Field(default=None, primary_key=True)
    language: str
    language_code: str = Field(index=True)
    title: str
    video_url: str
    video_drive_file_id: str
    music_track_id: int | None = Field(default=None, foreign_key="music_tracks.id")
    music_reused: bool = Field(default=False)
    pipeline_seconds: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    posted: bool = Field(default=False, index=True)
    published_at: datetime | None = Field(default=None)
