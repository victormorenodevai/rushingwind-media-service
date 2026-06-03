import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def get_duration(media_path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_entries", "format=duration",
            media_path,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[:500]}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def trim_audio(input_path: str, output_path: str, duration: float, fade: float = 2.0) -> None:
    fade_start = max(0.0, duration - fade)
    _run(
        [
            "ffmpeg", "-i", input_path,
            "-t", str(duration),
            "-af", f"afade=t=out:st={fade_start}:d={fade}",
            output_path, "-y",
        ],
        "trim_audio",
    )
    logger.info("Trimmed audio to %.1fs → %s", duration, output_path)


def _run(cmd: list[str], label: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg {label} failed (exit {result.returncode}):\n{result.stderr[-1000:]}")
    logger.info("FFmpeg %s OK", label)


def mix_audio(video_path: str, music_path: str, output_path: str) -> None:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Input video missing: {video_path}")
    if not os.path.exists(music_path):
        raise FileNotFoundError(f"Music file missing: {music_path}")

    _run(
        [
            "ffmpeg", "-i", video_path, "-i", music_path,
            "-filter_complex",
            "[0:a]volume=1.0[a1];[1:a]volume=0.35[a2];[a1][a2]amix=inputs=2:duration=first[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy",
            output_path, "-y",
        ],
        "mix_audio",
    )
    logger.info("Mixed audio written to %s", output_path)


def burn_subtitles(video_path: str, ass_path: str, output_path: str) -> None:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Input video missing: {video_path}")
    if not os.path.exists(ass_path):
        raise FileNotFoundError(f"ASS subtitle file missing: {ass_path}")

    # Use absolute path for ASS filter to avoid ffmpeg working-directory issues
    abs_ass = os.path.abspath(ass_path)

    _run(
        [
            "ffmpeg", "-i", video_path,
            "-vf", f"ass={abs_ass}",
            "-c:a", "copy",
            output_path, "-y",
        ],
        "burn_subtitles",
    )
    logger.info("Subtitles burned into %s", output_path)


def _get_video_dimensions(media_path: str) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json", media_path,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe dimensions failed: {result.stderr[:500]}")
    stream = json.loads(result.stdout)["streams"][0]
    return stream["width"], stream["height"]


def add_watermark(
    video_path: str,
    watermark_path: str,
    output_path: str,
    size_pct: float = 0.11,         # % del ancho del video
    margin_top_pct: float = 0.14,   # % del alto del video desde arriba
    margin_right_pct: float = 0.07, # % del ancho del video desde la derecha
    opacity: float = 0.85,
) -> None:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Input video missing: {video_path}")
    if not os.path.exists(watermark_path):
        raise FileNotFoundError(f"Watermark image missing: {watermark_path}")

    vid_w, vid_h = _get_video_dimensions(video_path)
    logo_w  = int(vid_w * size_pct)
    margin_r = int(vid_w * margin_right_pct)
    margin_t = int(vid_h * margin_top_pct)

    # scale logo to absolute px (h=-1 keeps aspect ratio), then overlay top-right
    vf = (
        f"movie={os.path.abspath(watermark_path)},"
        f"scale={logo_w}:-1,"
        f"format=rgba,colorchannelmixer=aa={opacity}"
        f"[wm];[in][wm]overlay=W-w-{margin_r}:{margin_t}"
    )

    _run(
        [
            "ffmpeg", "-i", video_path,
            "-vf", vf,
            "-c:a", "copy",
            output_path, "-y",
        ],
        "add_watermark",
    )
    logger.info("Watermark added (%dx%d video, logo_w=%dpx) → %s", vid_w, vid_h, logo_w, output_path)
