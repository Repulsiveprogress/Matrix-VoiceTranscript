from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from nio import AsyncClient, RoomMessageAudio, RoomMessageFile

from src.audio_converter import AudioConverter
from src.config import Settings
from src.strings import Strings
from src.transcriber import Transcriber

logger = logging.getLogger(__name__)

# PRIVACY CONTRACT: transcribed text must never appear in log output.
# Do not pass transcript content to any logger at any level.


class BotService:
    def __init__(
        self,
        settings: Settings,
        matrix: AsyncClient,
        transcriber: Transcriber,
        strings: Strings,
    ) -> None:
        self.settings = settings
        self.matrix = matrix
        self.transcriber = transcriber
        self.strings = strings
        self.started_at_ms = int(time.time() * 1000)
        self._converter = AudioConverter(matrix, settings.max_audio_bytes)
        # Single worker serialises ASR jobs - prevents OOM from concurrent model copies
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")

    def is_fresh_event(self, server_ts_ms: int | None) -> bool:
        if server_ts_ms is None:
            return True
        return int(server_ts_ms) >= self.started_at_ms

    async def handle_audio_event(
        self,
        room: object,
        event: RoomMessageAudio | RoomMessageFile,
    ) -> None:
        """Full pipeline: download, convert, transcribe, reply."""
        ts = getattr(event, "server_timestamp", None)
        if not self.is_fresh_event(ts):
            return
        if event.sender == self.matrix.user_id:
            return

        content: dict = event.source.get("content", {})
        info: dict = content.get("info") or {}
        mime: str = info.get("mimetype", "audio/ogg")
        file_size: int = info.get("size", 0)

        # Encrypted rooms use content["file"] with embedded key material;
        # unencrypted rooms use content["url"].
        encrypted_file: dict | None = content.get("file")
        if encrypted_file:
            mxc_url = encrypted_file.get("url")
        else:
            mxc_url = getattr(event, "url", None) or content.get("url")

        if not mxc_url or not str(mxc_url).startswith("mxc://"):
            logger.warning("Audio event has no mxc url room=%s", room.room_id)  # type: ignore[attr-defined]
            return

        if file_size and file_size > self.settings.max_audio_bytes:
            max_mb = self.settings.max_audio_bytes // (1024 * 1024)
            await self._send_plain(room.room_id, self.strings.audio_too_large.format(max_mb=max_mb))  # type: ignore[attr-defined]
            return

        logger.info(
            "Processing audio event room=%s sender=%s mime=%s encrypted=%s",
            room.room_id,  # type: ignore[attr-defined]
            event.sender,
            mime,
            encrypted_file is not None,
        )

        audio_bytes = await self._converter.download_mxc(str(mxc_url), encrypted_file)
        if audio_bytes is None:
            await self._send_plain(room.room_id, self.strings.no_audio_url)  # type: ignore[attr-defined]
            return

        loop = asyncio.get_event_loop()
        wav_path: str | None = await loop.run_in_executor(
            self._executor,
            self._converter.convert_to_wav,
            audio_bytes,
            mime,
        )
        if wav_path is None:
            await self._send_plain(room.room_id, self.strings.unsupported_format)  # type: ignore[attr-defined]
            return

        try:
            transcript: str = await loop.run_in_executor(
                self._executor,
                self.transcriber.transcribe_wav,
                wav_path,
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(wav_path)

        if not transcript or not transcript.strip():
            await self._send_plain(room.room_id, self.strings.transcription_failed)  # type: ignore[attr-defined]
            return

        reply = self.strings.transcription_result.format(text=transcript.strip())
        await self._send_plain(room.room_id, reply)  # type: ignore[attr-defined]
        logger.info(
            "Transcription complete room=%s sender=%s chars=%d",
            room.room_id,  # type: ignore[attr-defined]
            event.sender,
            len(transcript),
        )

    async def _send_plain(self, room_id: str, text: str) -> None:
        await self.matrix.room_send(
            room_id,
            "m.room.message",
            {"msgtype": "m.text", "body": text},
            ignore_unverified_devices=True,
        )
