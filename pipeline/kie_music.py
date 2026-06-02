import asyncio
import logging
import time
import httpx

from config import settings

logger = logging.getLogger(__name__)

KIE_GENERATE_URL = "https://api.kie.ai/api/v1/generate"
KIE_STATUS_URL = "https://api.kie.ai/api/v1/generate/record-info"
KIE_MODEL = "V4_5"  # opciones: V4, V4_5, V4_5PLUS, V4_5ALL, V5, V5_5


class KIETimeoutError(Exception):
    pass


async def generate_and_download(
    style_prompt: str,
    output_path: str,
    api_key: str,
    poll_interval: int = 30,
    timeout_seconds: int = 600,
) -> None:
    task_id = await _submit_music(style_prompt, api_key)
    music_url = await _poll_until_ready(task_id, api_key, poll_interval, timeout_seconds)
    await _download(music_url, output_path)


async def _submit_music(style_prompt: str, api_key: str) -> str:
    payload = {
        "prompt": style_prompt,
        "style": style_prompt,
        "title": "Background Music",
        "customMode": True,
        "instrumental": True,
        "model": KIE_MODEL,
        "callBackUrl": settings.KIE_CALLBACK_URL,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            KIE_GENERATE_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    if resp.status_code != 200:
        raise RuntimeError(f"KIE submit failed ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    task_id = data.get("data", {}).get("taskId") or data.get("taskId")
    if not task_id:
        raise RuntimeError(f"KIE returned no taskId: {data}")

    logger.info("KIE music submitted: taskId=%s", task_id)
    return task_id


async def _poll_until_ready(
    task_id: str,
    api_key: str,
    poll_interval: int,
    timeout_seconds: int,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    attempt = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while time.monotonic() < deadline:
            attempt += 1
            await asyncio.sleep(poll_interval)

            resp = await client.get(
                KIE_STATUS_URL,
                params={"taskId": task_id},
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if resp.status_code != 200:
                logger.warning("KIE poll attempt %d got HTTP %d", attempt, resp.status_code)
                continue

            data = resp.json()
            inner = data.get("data") or {}
            records = (inner.get("response") or {}).get("sunoData", [])
            status = (inner.get("status") or "").upper()
            logger.info("KIE [%s] poll #%d status=%s", task_id, attempt, status)

            if status in ("SUCCESS", "FIRST_SUCCESS") and records:
                # audioUrl se llena al terminar, streamAudioUrl está disponible antes
                audio_url = records[0].get("audioUrl") or records[0].get("streamAudioUrl")
                if audio_url:
                    return audio_url

            if status in ("FAILED", "ERROR"):
                raise RuntimeError(f"KIE music generation failed for taskId={task_id}: {data}")

    raise KIETimeoutError(
        f"KIE music taskId={task_id} timed out after {timeout_seconds}s ({attempt} polls)"
    )


async def _download(url: str, output_path: str) -> None:
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

    logger.info("KIE music downloaded to %s", output_path)
