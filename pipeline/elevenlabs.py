import logging
import httpx

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

logger = logging.getLogger(__name__)


async def generate_tts(text: str, voice_id: str, api_key: str) -> bytes:
    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs TTS failed ({resp.status_code}): {resp.text[:300]}"
        )

    logger.info("TTS generated: %d bytes", len(resp.content))
    return resp.content
