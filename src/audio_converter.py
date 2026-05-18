from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from urllib.parse import quote

import aiohttp
from nio import AsyncClient
from nio.crypto.attachments import decrypt_attachment

logger = logging.getLogger(__name__)

_MIME_TO_PYDUB: dict[str, str] = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "application/ogg": "ogg",
    "audio/webm": "webm",
    "audio/mp4": "mp4",
    "audio/m4a": "mp4",
    "audio/x-m4a": "mp4",
    "audio/aac": "aac",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
}


def _pydub_format(mime: str) -> str:
    return _MIME_TO_PYDUB.get(mime.lower().split(";")[0].strip(), "ogg")


def _input_suffix(mime: str) -> str:
    fmt = _pydub_format(mime)
    return {
        "mp4": ".m4a",
        "webm": ".webm",
        "ogg": ".ogg",
        "mp3": ".mp3",
        "flac": ".flac",
        "wav": ".wav",
        "aac": ".aac",
    }.get(fmt, ".bin")


def _parse_mxc(mxc_url: str) -> tuple[str, str] | None:
    if not mxc_url.startswith("mxc://"):
        return None
    parts = mxc_url[len("mxc://") :].split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


class AudioConverter:
    """Downloads Matrix audio content and converts it to 16 kHz mono WAV."""

    def __init__(self, matrix: AsyncClient, max_bytes: int) -> None:
        self._matrix = matrix
        self._max_bytes = max_bytes

    async def download_mxc(
        self,
        mxc_url: str,
        encrypted_file: dict | None = None,
    ) -> bytes | None:
        """Download mxc:// URL and optionally decrypt an E2EE attachment.

        Pass the content["file"] dict from the Matrix event for encrypted rooms.
        """
        parsed = _parse_mxc(mxc_url)
        if parsed is None:
            logger.warning("Invalid mxc URL: %s", mxc_url)
            return None
        server, media_id = parsed

        homeserver = self._matrix.homeserver.rstrip("/")
        token = self._matrix.access_token

        urls = [
            f"{homeserver}/_matrix/client/v1/media/download/{quote(server)}/{quote(media_id)}",
            f"{homeserver}/_matrix/media/v3/download/{quote(server)}/{quote(media_id)}",
            f"{homeserver}/_matrix/media/r0/download/{quote(server)}/{quote(media_id)}",
        ]

        headers = {"Authorization": f"Bearer {token}"} if token else {}

        async with aiohttp.ClientSession() as session:
            for url in urls:
                try:
                    async with session.get(url, headers=headers, allow_redirects=True) as resp:
                        if resp.status != 200:
                            logger.info("Download endpoint %s returned %d", url, resp.status)
                            continue
                        content_length = resp.content_length
                        if content_length and content_length > self._max_bytes:
                            logger.warning("Audio too large: %d bytes", content_length)
                            return None
                        # Read full body, comparing against declared Content-Length to
                        # catch truncated responses from Synapse's media replication.
                        data = await resp.read()
                        if len(data) > self._max_bytes:
                            logger.warning("Audio exceeds max bytes after download")
                            return None
                        if not data:
                            logger.warning("Empty body from %s", url)
                            continue
                        if content_length and len(data) < content_length:
                            logger.warning(
                                "Truncated response from %s: got %d / %d bytes",
                                url,
                                len(data),
                                content_length,
                            )
                            continue
                        content_type = resp.headers.get("Content-Type", "?")
                        logger.info(
                            "Downloaded %d bytes from %s (Content-Type=%s, declared=%s)",
                            len(data),
                            url,
                            content_type,
                            content_length,
                        )
                        # Suspiciously small response that isn't audio - likely a JSON error
                        if len(data) < 1024 and not content_type.startswith("audio/"):
                            preview = data[:512].decode("utf-8", errors="replace")
                            logger.warning("Non-audio short response body: %s", preview)
                            continue
                        # Validate OGG signature when MIME claims ogg - Synapse sometimes
                        # serves stub responses on cache miss
                        if content_type.startswith("audio/ogg") and not data.startswith(b"OggS"):
                            preview = data[:64].hex()
                            logger.warning(
                                "Response claims ogg but missing OggS magic from %s: %s",
                                url,
                                preview,
                            )
                            continue
                        if encrypted_file:
                            try:
                                data = decrypt_attachment(data, encrypted_file)
                            except Exception:
                                logger.exception("Failed to decrypt attachment mxc=%s", mxc_url)
                                return None
                        return data
                except aiohttp.ClientError:
                    logger.exception("Download failed for %s", url)
                    continue

        logger.error("All download URLs failed for mxc: %s", mxc_url)
        return None

    def convert_to_wav(self, audio_bytes: bytes, mime: str) -> str | None:
        """Convert raw audio bytes to a temporary 16 kHz mono WAV file.

        Returns the temp file path, or None on failure.
        Caller must delete the file (e.g. via os.unlink in a finally block).
        Blocking - must be called via run_in_executor.
        """
        import shutil
        import subprocess

        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

        # Write to disk first - ffmpeg's pipe input is unreliable with some
        # ogg/opus payloads on ffmpeg 7.x, while file input is rock-solid.
        with tempfile.NamedTemporaryFile(suffix=_input_suffix(mime), delete=False) as src:
            src.write(audio_bytes)
            src_path = src.name

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst:
            dst_path = dst.name

        try:
            result = subprocess.run(  # noqa: S603
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    src_path,
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-sample_fmt",
                    "s16",
                    dst_path,
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                logger.error(
                    "ffmpeg failed (mime=%s, rc=%d): %s",
                    mime,
                    result.returncode,
                    result.stderr.decode("utf-8", errors="replace").strip(),
                )
                with contextlib.suppress(OSError):
                    os.unlink(dst_path)
                return None
            return dst_path
        finally:
            with contextlib.suppress(OSError):
                os.unlink(src_path)
