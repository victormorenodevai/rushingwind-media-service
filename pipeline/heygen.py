import asyncio
import logging
import time
import httpx

logger = logging.getLogger(__name__)

HEYGEN_BASE = "https://api.heygen.com"
HEYGEN_UPLOAD_URL = "https://upload.heygen.com/v1/asset"


class HeyGenFailedError(Exception):
    pass


class HeyGenTimeoutError(Exception):
    pass


async def upload_audio_asset(audio_bytes: bytes, api_key: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            HEYGEN_UPLOAD_URL,
            content=audio_bytes,
            headers={
                "X-Api-Key": api_key,
                "Content-Type": "audio/mpeg",
            },
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"HeyGen asset upload failed ({resp.status_code}): {resp.text[:300]}"
        )

    data = resp.json()
    asset_id = data.get("data", {}).get("id") or data.get("id")
    if not asset_id:
        raise RuntimeError(f"HeyGen upload returned no asset_id: {data}")

    logger.info("HeyGen audio asset uploaded: %s", asset_id)
    return asset_id


async def generate_video(asset_id: str, avatar_id: str, api_key: str) -> str:
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "audio",
                    "audio_asset_id": asset_id,
                },
            }
        ],
        "dimension": {"width": 720, "height": 1280},
        "aspect_ratio": "9:16",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{HEYGEN_BASE}/v2/video/generate",
            json=payload,
            headers={
                "X-Api-Key": api_key,
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"HeyGen video generate failed ({resp.status_code}): {resp.text[:300]}"
        )

    data = resp.json()
    video_id = data.get("data", {}).get("video_id") or data.get("video_id")
    if not video_id:
        raise RuntimeError(f"HeyGen generate returned no video_id: {data}")

    logger.info("HeyGen video submitted: %s", video_id)
    return video_id


async def poll_until_complete(
    video_id: str,
    api_key: str,
    poll_interval: int = 60,
    timeout_seconds: int = 1500,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    attempt = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while time.monotonic() < deadline:
            attempt += 1
            await asyncio.sleep(poll_interval)

            resp = await client.get(
                f"{HEYGEN_BASE}/v1/video_status.get",
                params={"video_id": video_id},
                headers={"X-Api-Key": api_key},
            )

            if resp.status_code != 200:
                logger.warning(
                    "HeyGen poll attempt %d got HTTP %d, continuing",
                    attempt,
                    resp.status_code,
                )
                continue

            data = resp.json().get("data", {})
            status = data.get("status")
            logger.info("HeyGen [%s] poll #%d status=%s", video_id, attempt, status)

            if status == "completed":
                video_url = data.get("video_url")
                if not video_url:
                    raise RuntimeError(f"HeyGen completed but no video_url in response: {data}")
                return video_url

            if status == "failed":
                error_msg = data.get("error", {})
                raise HeyGenFailedError(
                    f"HeyGen video {video_id} failed: {error_msg}"
                )

    raise HeyGenTimeoutError(
        f"HeyGen video {video_id} timed out after {timeout_seconds}s ({attempt} polls)"
    )


async def download_video(video_url: str, output_path: str, api_key: str) -> None:
    async with httpx.AsyncClient(
        timeout=300,
        headers={"X-Api-Key": api_key},
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", video_url) as resp:
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

    logger.info("HeyGen video downloaded to %s", output_path)
