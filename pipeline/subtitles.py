import logging
import os
from typing import Generator

from faster_whisper import WhisperModel

from config import settings

logger = logging.getLogger(__name__)

WORDS_PER_LINE = 3
FONT_NAME = "Helvetica Neue"
FONT_SIZE = 60
RESOLUTION = (720, 1280)
MARGIN_V = 250  # px desde el borde inferior — sube para subir el texto
MARGIN_L = 50   # px desde el borde izquierdo
MARGIN_R = 50   # px desde el borde derecho

# Loaded once at module level — heavy model, not per-request
_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        model_name = settings.WHISPER_MODEL
        logger.info("Loading faster-whisper model (%s, CPU)", model_name)
        _model = WhisperModel(model_name, device="cpu", compute_type="int8")
    return _model


def _format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _group_words(words: list, group_size: int) -> Generator[list, None, None]:
    for i in range(0, len(words), group_size):
        yield words[i : i + group_size]


def _build_ass_header() -> str:
    return f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {RESOLUTION[0]}
PlayResY: {RESOLUTION[1]}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT_NAME},{FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,{MARGIN_L},{MARGIN_R},{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def generate_ass(video_path: str, language_code: str, output_path: str) -> str:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video missing for transcription: {video_path}")

    model = _get_model()
    segments, _ = model.transcribe(
        video_path,
        language=language_code,
        word_timestamps=True,
        vad_filter=True,      # skip silence / background-only sections
        beam_size=5,
    )

    all_words = []
    for segment in segments:
        if segment.words:
            all_words.extend(segment.words)

    if not all_words:
        logger.warning("No words transcribed from %s — writing empty subtitle file", video_path)

    lines = [_build_ass_header()]

    for group in _group_words(all_words, WORDS_PER_LINE):
        start = _format_ass_time(group[0].start)
        end = _format_ass_time(group[-1].end)
        text = " ".join(w.word.strip() for w in group).upper()
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("ASS subtitle file written: %s (%d groups)", output_path, len(all_words) // WORDS_PER_LINE)
    return output_path
