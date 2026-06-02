import logging
import os
import random
import tempfile
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from sqlmodel import select

from config import settings
from database import get_session, init_db
from db_models import MusicTrack, Production
from models import MusicPickRequest, MusicPickResponse, VideoRequest, VideoResponse
from pipeline import cleanup, elevenlabs, ffmpeg_mixer, gdrive, heygen, kie_music, subtitles

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.DATABASE_URL:
        await init_db()
        logger.info("Database tables ready")
    yield


app = FastAPI(title="media-service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/productions")
async def list_productions(limit: int = 20, language_code: str | None = None):
    async with get_session() as session:
        query = select(Production).order_by(Production.created_at.desc()).limit(limit)
        if language_code:
            query = query.where(Production.language_code == language_code)
        results = (await session.exec(query)).all()
    return results


@app.post("/pick-music", response_model=MusicPickResponse)
async def pick_music(req: MusicPickRequest):
    """Resolve music once before launching parallel /produce-video calls."""
    tmp_dir = tempfile.mkdtemp(prefix="rw_music_")
    try:
        music_full_path = os.path.join(tmp_dir, "music_full.mp3")
        library_folder = settings.GDRIVE_MUSIC_LIBRARY_FOLDER_ID

        # Query DB for current library (fast — no Drive API call)
        async with get_session() as session:
            tracks = (await session.exec(select(MusicTrack))).all()
        library_count = len(tracks)

        if tracks and library_count >= settings.MUSIC_LIBRARY_SIZE:
            # Library full — pick a random track and download from Drive
            track = random.choice(tracks)
            gdrive.download_file(track.drive_file_id, music_full_path, settings.GDRIVE_CREDENTIALS_JSON)
            async with get_session() as session:
                t = await session.get(MusicTrack, track.id)
                t.times_used += 1
                session.add(t)
                await session.commit()
            logger.info("pick_music: reused %s (used %d times)", track.filename, track.times_used + 1)
            return MusicPickResponse(
                music_drive_url=f"https://drive.google.com/uc?export=download&id={track.drive_file_id}",
                music_drive_file_id=track.drive_file_id,
                reused=True,
                library_count=library_count,
            )
        else:
            # Library incomplete — generate a new song with KIE
            if not library_folder:
                raise RuntimeError("GDRIVE_MUSIC_LIBRARY_FOLDER_ID not set — cannot store music")
            await kie_music.generate_and_download(
                req.music_style_prompt,
                music_full_path,
                settings.KIE_API_KEY,
                timeout_seconds=settings.KIE_POLL_TIMEOUT,
            )
            song_name = f"song_{library_count + 1:03d}.mp3"
            file_id, _ = gdrive.upload_file(
                music_full_path, song_name, library_folder,
                settings.GDRIVE_CREDENTIALS_JSON, mimetype="audio/mpeg",
            )
            async with get_session() as session:
                track = MusicTrack(drive_file_id=file_id, filename=song_name)
                session.add(track)
                await session.commit()
                await session.refresh(track)
            library_count += 1
            logger.info("pick_music: new song saved %s (%d total)", song_name, library_count)
            return MusicPickResponse(
                music_drive_url=f"https://drive.google.com/uc?export=download&id={file_id}",
                music_drive_file_id=file_id,
                reused=False,
                library_count=library_count,
            )

    except Exception as exc:
        logger.exception("pick_music failed: %s", exc)
        raise HTTPException(status_code=500, detail={"status": "failed", "error": str(exc)})

    finally:
        cleanup.delete_dir(tmp_dir)


@app.post("/produce-video", response_model=VideoResponse)
async def produce_video(req: VideoRequest):
    logger.info("Starting video production [%s]", req.language)
    tmp_dir = tempfile.mkdtemp(prefix=f"rw_{req.language_code}_")
    temp_files: list[str] = []
    steps_done: list[str] = []
    start = time.monotonic()

    try:
        # Step 1: TTS
        audio_bytes = await elevenlabs.generate_tts(
            req.text, req.voice_id, settings.ELEVENLABS_API_KEY
        )
        audio_path = os.path.join(tmp_dir, "voice.mp3")
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        temp_files.append(audio_path)
        steps_done.append("tts")
        logger.info("[%s] TTS done", req.language)

        # Step 2: HeyGen — upload audio asset, generate video, poll, download
        asset_id = await heygen.upload_audio_asset(audio_bytes, settings.HEYGEN_API_KEY)
        video_id = await heygen.generate_video(asset_id, req.avatar_id, settings.HEYGEN_API_KEY)
        heygen_url = await heygen.poll_until_complete(
            video_id,
            settings.HEYGEN_API_KEY,
            timeout_seconds=settings.HEYGEN_POLL_TIMEOUT,
        )
        raw_video_path = os.path.join(tmp_dir, "raw.mp4")
        await heygen.download_video(heygen_url, raw_video_path, settings.HEYGEN_API_KEY)
        temp_files.append(raw_video_path)
        steps_done.append("heygen")
        logger.info("[%s] HeyGen done", req.language)

        # Step 3: Music — use URL provided by /pick-music, or fall back to library logic
        music_full_path = os.path.join(tmp_dir, "music_full.mp3")
        music_reused = False
        music_library_count = 0
        music_track_id: int | None = None

        if req.music_drive_url:
            # n8n called /pick-music first and passed the URL — just download it
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                async with client.stream("GET", req.music_drive_url) as resp:
                    resp.raise_for_status()
                    with open(music_full_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(65536):
                            f.write(chunk)
            music_reused = True
            steps_done.append("music_from_url")
            logger.info("[%s] Music downloaded from provided URL", req.language)
        else:
            # Standalone call — resolve music internally via library
            library_folder = settings.GDRIVE_MUSIC_LIBRARY_FOLDER_ID
            if library_folder:
                songs = gdrive.list_mp3s(library_folder, settings.GDRIVE_CREDENTIALS_JSON)
                music_library_count = len(songs)

            async with get_session() as session:
                tracks = (await session.exec(select(MusicTrack))).all()
                music_library_count = len(tracks)

            if tracks and music_library_count >= settings.MUSIC_LIBRARY_SIZE:
                track = random.choice(tracks)
                gdrive.download_file(track.drive_file_id, music_full_path, settings.GDRIVE_CREDENTIALS_JSON)
                music_track_id = track.id
                music_reused = True
                steps_done.append("kie_music_reused")
                logger.info("[%s] Music reused: %s", req.language, track.filename)
            else:
                await kie_music.generate_and_download(
                    req.music_style_prompt,
                    music_full_path,
                    settings.KIE_API_KEY,
                    timeout_seconds=settings.KIE_POLL_TIMEOUT,
                )
                if library_folder:
                    song_name = f"song_{music_library_count + 1:03d}.mp3"
                    new_file_id, _ = gdrive.upload_file(
                        music_full_path, song_name, library_folder,
                        settings.GDRIVE_CREDENTIALS_JSON, mimetype="audio/mpeg",
                    )
                    async with get_session() as session:
                        new_track = MusicTrack(drive_file_id=new_file_id, filename=song_name)
                        session.add(new_track)
                        await session.commit()
                        await session.refresh(new_track)
                    music_track_id = new_track.id
                    music_library_count += 1
                    logger.info("[%s] New song saved: %s (%d total)", req.language, song_name, music_library_count)
                steps_done.append("kie_music_new")

        temp_files.append(music_full_path)

        # Trim music to video duration + 2s (smooth fade-out)
        video_duration = ffmpeg_mixer.get_duration(raw_video_path)
        music_trim_path = os.path.join(tmp_dir, "music_trim.mp3")
        ffmpeg_mixer.trim_audio(music_full_path, music_trim_path, video_duration + 2.0)
        temp_files.append(music_trim_path)

        # Step 4: FFmpeg — mix voice video with background music
        mixed_path = os.path.join(tmp_dir, "mixed.mp4")
        ffmpeg_mixer.mix_audio(raw_video_path, music_trim_path, mixed_path)
        temp_files.append(mixed_path)
        steps_done.append("ffmpeg_mix")
        logger.info("[%s] FFmpeg mix done", req.language)

        # Step 5: faster-whisper — generate karaoke ASS subtitle file
        ass_path = os.path.join(tmp_dir, "subs.ass")
        subtitles.generate_ass(raw_video_path, req.language_code, ass_path)
        temp_files.append(ass_path)
        steps_done.append("subtitles")
        logger.info("[%s] Subtitles done", req.language)

        # Step 6: FFmpeg — burn subtitles into video
        final_path = os.path.join(tmp_dir, "final.mp4")
        ffmpeg_mixer.burn_subtitles(mixed_path, ass_path, final_path)
        temp_files.append(final_path)
        steps_done.append("ffmpeg_burn")
        logger.info("[%s] FFmpeg subtitle burn done", req.language)

        # Step 7: Upload to Google Drive
        file_id = gdrive.upload_video(
            final_path,
            f"{req.title}.mp4",
            req.drive_folder_id,
            settings.GDRIVE_CREDENTIALS_JSON,
        )
        steps_done.append("gdrive_upload")
        logger.info("[%s] Drive upload done → %s", req.language, file_id)

        # Record production in DB
        if settings.DATABASE_URL:
            async with get_session() as session:
                prod = Production(
                    language=req.language,
                    language_code=req.language_code,
                    title=req.title,
                    video_url=f"https://drive.google.com/uc?export=download&id={file_id}",
                    video_drive_file_id=file_id,
                    music_track_id=music_track_id,
                    music_reused=music_reused,
                    pipeline_seconds=int(time.monotonic() - start),
                )
                session.add(prod)
                await session.commit()

        return VideoResponse(
            status="complete",
            language=req.language,
            video_url=f"https://drive.google.com/uc?export=download&id={file_id}",
            drive_file_id=file_id,
            duration_seconds=int(time.monotonic() - start),
            steps_completed=steps_done,
            music_reused=music_reused,
            music_library_count=music_library_count,
        )

    except Exception as exc:
        failed_at = _next_step(steps_done)
        logger.exception("[%s] Failed at %s: %s", req.language, failed_at, exc)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "failed",
                "language": req.language,
                "failed_at": failed_at,
                "error": str(exc),
                "steps_completed": steps_done,
            },
        )

    finally:
        cleanup.delete_files(temp_files)
        cleanup.delete_dir(tmp_dir)


def _next_step(steps_done: list[str]) -> str:
    order = ["tts", "heygen", "kie_music_new", "kie_music_reused", "ffmpeg_mix", "subtitles", "ffmpeg_burn", "gdrive_upload"]
    for step in order:
        if step not in steps_done:
            return step
    return "unknown"
