import logging
import os
import random
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import select

from config import settings
from database import engine as db_engine, get_session, init_db
from db_models import MusicTrack, Production
from models import (
    MusicPickRequest, MusicPickResponse,
    VideoRequest, VideoResponse,
    ProcessVideoRequest, ProcessVideoResponse,
    ProductionCreate, MusicTrackUpdate,
)
from pipeline import cleanup, elevenlabs, ffmpeg_mixer, heygen, kie_music, subtitles

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.STORAGE_DIR, exist_ok=True)
    os.makedirs(settings.MUSIC_DIR, exist_ok=True)
    if db_engine is not None:
        await init_db()
        logger.info("Database tables ready")
    yield


app = FastAPI(title="media-service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/kie-callback")
async def kie_callback(payload: dict):
    logger.info("KIE callback received: %s", payload)
    return {"status": "ok"}


@app.get("/productions")
async def list_productions(limit: int = 20, language_code: str | None = None):
    async with get_session() as session:
        query = select(Production).order_by(Production.created_at.desc()).limit(limit)
        if language_code:
            query = query.where(Production.language_code == language_code)
        results = (await session.execute(query)).scalars().all()
    return results


@app.post("/productions", status_code=201)
async def create_production(req: ProductionCreate):
    """Called by n8n after Drive upload is confirmed — registers production in DB."""
    if not settings.DATABASE_URL:
        raise HTTPException(status_code=503, detail="Database not configured")
    async with get_session() as session:
        prod = Production(
            language=req.language,
            language_code=req.language_code,
            title=req.title,
            video_url=req.video_url,
            video_drive_file_id=req.video_drive_file_id,
            music_track_id=req.music_track_id,
            music_reused=req.music_reused,
            pipeline_seconds=req.pipeline_seconds,
            created_at=datetime.utcnow(),
        )
        session.add(prod)
        await session.commit()
        await session.refresh(prod)
    return prod


@app.get("/files/{file_id}")
async def download_file(file_id: str):
    """Serve a produced video so n8n can download it and upload to Drive."""
    if not all(c.isalnum() or c == "-" for c in file_id):
        raise HTTPException(status_code=400, detail="Invalid file_id")
    path = os.path.join(settings.STORAGE_DIR, f"{file_id}.mp4")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found or already deleted")
    return FileResponse(path, media_type="video/mp4", filename=f"{file_id}.mp4")


@app.delete("/files/{file_id}", status_code=204)
async def delete_file(file_id: str):
    """Delete temp file after n8n has successfully uploaded it to Drive."""
    if not all(c.isalnum() or c == "-" for c in file_id):
        raise HTTPException(status_code=400, detail="Invalid file_id")
    path = os.path.join(settings.STORAGE_DIR, f"{file_id}.mp4")
    if os.path.exists(path):
        os.remove(path)
        logger.info("Deleted temp file: %s", file_id)


@app.get("/music/{filename}")
async def serve_music(filename: str):
    """Serve a music file so n8n can download it and upload to Drive."""
    if not all(c.isalnum() or c in "-_." for c in filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(settings.MUSIC_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Music file not found")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)


@app.patch("/music-tracks/{track_id}", status_code=200)
async def update_music_track(track_id: int, req: MusicTrackUpdate):
    """Called by n8n after uploading a new track to Drive — stores the Drive URL."""
    if not settings.DATABASE_URL:
        raise HTTPException(status_code=503, detail="Database not configured")
    async with get_session() as session:
        track = await session.get(MusicTrack, track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        track.drive_file_id = req.drive_file_id
        track.audio_url = req.audio_url
        session.add(track)
        await session.commit()
        await session.refresh(track)
    return track


@app.post("/pick-music", response_model=MusicPickResponse)
async def pick_music(req: MusicPickRequest):
    """Pick or generate a music track from the local library."""
    try:
        async with get_session() as session:
            tracks = (await session.execute(select(MusicTrack))).scalars().all()
        library_count = len(tracks)

        if tracks and library_count >= settings.MUSIC_LIBRARY_SIZE:
            track = random.choice(tracks)
            async with get_session() as session:
                t = await session.get(MusicTrack, track.id)
                t.times_used += 1
                session.add(t)
                await session.commit()
            logger.info("pick_music: reused %s (used %d times)", track.filename, t.times_used)
            return MusicPickResponse(
                music_filename=track.filename,
                music_track_id=track.id,
                reused=True,
                library_count=library_count,
            )
        else:
            song_name = f"song_{library_count + 1:03d}.mp3"
            dest_path = os.path.join(settings.MUSIC_DIR, song_name)
            try:
                await kie_music.generate_and_download(
                    req.music_style_prompt,
                    dest_path,
                    settings.KIE_API_KEY,
                    timeout_seconds=settings.KIE_POLL_TIMEOUT,
                )
            except Exception as kie_exc:
                if tracks:
                    # KIE failed (quota/timeout) but we have existing tracks — reuse one
                    logger.warning("KIE generation failed, falling back to library: %s", kie_exc)
                    track = random.choice(tracks)
                    async with get_session() as session:
                        t = await session.get(MusicTrack, track.id)
                        t.times_used += 1
                        session.add(t)
                        await session.commit()
                    return MusicPickResponse(
                        music_filename=track.filename,
                        music_track_id=track.id,
                        reused=True,
                        library_count=library_count,
                    )
                raise  # library is empty and KIE failed — nothing to fall back to

            async with get_session() as session:
                track = MusicTrack(filename=song_name, created_at=datetime.utcnow())
                session.add(track)
                await session.commit()
                await session.refresh(track)
            library_count += 1
            logger.info("pick_music: new song saved %s (%d total)", song_name, library_count)
            return MusicPickResponse(
                music_filename=song_name,
                music_track_id=track.id,
                reused=False,
                library_count=library_count,
            )

    except Exception as exc:
        logger.exception("pick_music failed: %s", exc)
        raise HTTPException(status_code=500, detail={"status": "failed", "error": str(exc)})


@app.post("/produce-video", response_model=VideoResponse)
async def produce_video(req: VideoRequest):
    logger.info("Starting video production [%s]", req.language)
    file_id = str(uuid.uuid4())
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

        # Step 2: HeyGen
        asset_id = await heygen.upload_audio_asset(audio_bytes, settings.HEYGEN_API_KEY)
        video_id = await heygen.generate_video(asset_id, req.avatar_id, settings.HEYGEN_API_KEY)
        heygen_url = await heygen.poll_until_complete(
            video_id, settings.HEYGEN_API_KEY, timeout_seconds=settings.HEYGEN_POLL_TIMEOUT,
        )
        raw_video_path = os.path.join(tmp_dir, "raw.mp4")
        await heygen.download_video(heygen_url, raw_video_path, settings.HEYGEN_API_KEY)
        temp_files.append(raw_video_path)
        steps_done.append("heygen")
        logger.info("[%s] HeyGen done", req.language)

        # Step 3: Music
        if req.music_filename:
            music_full_path = os.path.join(settings.MUSIC_DIR, req.music_filename)
            if not os.path.exists(music_full_path):
                raise FileNotFoundError(f"Music file not found: {req.music_filename}")
            steps_done.append("music_from_local")
        else:
            music_full_path = os.path.join(tmp_dir, "music_full.mp3")
            await kie_music.generate_and_download(
                req.music_style_prompt, music_full_path,
                settings.KIE_API_KEY, timeout_seconds=settings.KIE_POLL_TIMEOUT,
            )
            temp_files.append(music_full_path)
            steps_done.append("kie_music_new")
        logger.info("[%s] Music ready", req.language)

        # Step 4: Trim + mix
        video_duration = ffmpeg_mixer.get_duration(raw_video_path)
        music_trim_path = os.path.join(tmp_dir, "music_trim.mp3")
        ffmpeg_mixer.trim_audio(music_full_path, music_trim_path, video_duration + 2.0)
        temp_files.append(music_trim_path)

        mixed_path = os.path.join(tmp_dir, "mixed.mp4")
        ffmpeg_mixer.mix_audio(raw_video_path, music_trim_path, mixed_path)
        temp_files.append(mixed_path)
        steps_done.append("ffmpeg_mix")
        logger.info("[%s] FFmpeg mix done", req.language)

        # Step 5: Subtitles
        ass_path = os.path.join(tmp_dir, "subs.ass")
        subtitles.generate_ass(raw_video_path, req.language_code, ass_path)
        temp_files.append(ass_path)
        steps_done.append("subtitles")
        logger.info("[%s] Subtitles done", req.language)

        # Step 6: Burn subtitles
        subs_path = os.path.join(tmp_dir, "subs_burned.mp4")
        ffmpeg_mixer.burn_subtitles(mixed_path, ass_path, subs_path)
        temp_files.append(subs_path)
        steps_done.append("ffmpeg_burn")

        # Step 7: Watermark
        upload_path = subs_path
        if settings.LOGO_PATH and os.path.exists(settings.LOGO_PATH):
            wm_path = os.path.join(tmp_dir, "watermarked.mp4")
            ffmpeg_mixer.add_watermark(subs_path, settings.LOGO_PATH, wm_path)
            temp_files.append(wm_path)
            upload_path = wm_path
            steps_done.append("watermark")

        # Step 8: Save to storage — n8n downloads, uploads to Drive, then calls DELETE + POST /productions
        final_path = os.path.join(settings.STORAGE_DIR, f"{file_id}.mp4")
        shutil.copy2(upload_path, final_path)
        steps_done.append("saved_to_storage")
        logger.info("[%s] Saved to storage → %s", req.language, file_id)

        return VideoResponse(
            status="complete",
            language=req.language,
            file_id=file_id,
            download_url=f"{settings.BASE_URL}/files/{file_id}",
            duration_seconds=int(time.monotonic() - start),
            steps_completed=steps_done,
            music_reused=req.music_filename is not None,
        )

    except Exception as exc:
        failed_at = _next_step(steps_done)
        logger.exception("[%s] Failed at %s: %s", req.language, failed_at, exc)
        _cleanup_storage(file_id)
        raise HTTPException(
            status_code=500,
            detail={"status": "failed", "language": req.language, "failed_at": failed_at,
                    "error": str(exc), "steps_completed": steps_done},
        )
    finally:
        cleanup.delete_files(temp_files)
        cleanup.delete_dir(tmp_dir)


@app.post("/process-video", response_model=ProcessVideoResponse)
async def process_video(req: ProcessVideoRequest):
    """Post-produce a pastor-recorded video: add music, subtitles, logo, save locally."""
    logger.info("Starting process-video [%s] — %s", req.language_code, req.title)
    file_id = str(uuid.uuid4())
    tmp_dir = tempfile.mkdtemp(prefix="rw_proc_")
    temp_files: list[str] = []
    steps_done: list[str] = []
    start = time.monotonic()

    try:
        # Step 1: Download input video
        input_path = os.path.join(tmp_dir, "input.mp4")
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream("GET", req.video_url) as resp:
                resp.raise_for_status()
                with open(input_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(65536):
                        f.write(chunk)
        temp_files.append(input_path)
        steps_done.append("download")

        # Step 2: Music
        if req.music_filename:
            music_full_path = os.path.join(settings.MUSIC_DIR, req.music_filename)
            if not os.path.exists(music_full_path):
                raise FileNotFoundError(f"Music file not found: {req.music_filename}")
            steps_done.append("music_from_local")
        else:
            music_full_path = os.path.join(tmp_dir, "music_full.mp3")
            await kie_music.generate_and_download(
                req.music_style_prompt, music_full_path,
                settings.KIE_API_KEY, timeout_seconds=settings.KIE_POLL_TIMEOUT,
            )
            temp_files.append(music_full_path)
            steps_done.append("kie_music_new")

        # Step 3: Trim + mix
        video_duration = ffmpeg_mixer.get_duration(input_path)
        music_trim_path = os.path.join(tmp_dir, "music_trim.mp3")
        ffmpeg_mixer.trim_audio(music_full_path, music_trim_path, video_duration + 2.0)
        temp_files.append(music_trim_path)

        mixed_path = os.path.join(tmp_dir, "mixed.mp4")
        ffmpeg_mixer.mix_audio(input_path, music_trim_path, mixed_path)
        temp_files.append(mixed_path)
        steps_done.append("ffmpeg_mix")

        # Step 4: Subtitles
        ass_path = os.path.join(tmp_dir, "subs.ass")
        subtitles.generate_ass(input_path, req.language_code, ass_path)
        temp_files.append(ass_path)
        steps_done.append("subtitles")

        # Step 5: Burn subtitles
        subs_path = os.path.join(tmp_dir, "subs_burned.mp4")
        ffmpeg_mixer.burn_subtitles(mixed_path, ass_path, subs_path)
        temp_files.append(subs_path)
        steps_done.append("ffmpeg_burn")

        # Step 6: Watermark
        upload_path = subs_path
        if settings.LOGO_PATH and os.path.exists(settings.LOGO_PATH):
            wm_path = os.path.join(tmp_dir, "watermarked.mp4")
            ffmpeg_mixer.add_watermark(subs_path, settings.LOGO_PATH, wm_path)
            temp_files.append(wm_path)
            upload_path = wm_path
            steps_done.append("watermark")

        # Step 7: Save to storage — n8n downloads, uploads to Drive, then calls DELETE + POST /productions
        final_path = os.path.join(settings.STORAGE_DIR, f"{file_id}.mp4")
        shutil.copy2(upload_path, final_path)
        steps_done.append("saved_to_storage")
        logger.info("Saved to storage → %s", file_id)

        return ProcessVideoResponse(
            status="complete",
            file_id=file_id,
            download_url=f"{settings.BASE_URL}/files/{file_id}",
            duration_seconds=int(time.monotonic() - start),
            steps_completed=steps_done,
        )

    except Exception as exc:
        logger.exception("process_video failed: %s", exc)
        _cleanup_storage(file_id)
        raise HTTPException(status_code=500, detail={
            "status": "failed", "error": str(exc), "steps_completed": steps_done,
        })
    finally:
        cleanup.delete_files(temp_files)
        cleanup.delete_dir(tmp_dir)


def _cleanup_storage(file_id: str) -> None:
    path = os.path.join(settings.STORAGE_DIR, f"{file_id}.mp4")
    if os.path.exists(path):
        os.remove(path)


def _next_step(steps_done: list[str]) -> str:
    order = ["tts", "heygen", "music_from_local", "kie_music_new",
             "ffmpeg_mix", "subtitles", "ffmpeg_burn", "watermark", "saved_to_storage"]
    for step in order:
        if step not in steps_done:
            return step
    return "unknown"
