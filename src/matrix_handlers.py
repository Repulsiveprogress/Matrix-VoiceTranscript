from __future__ import annotations

import contextlib
import logging
from collections import defaultdict

from nio import (
    AsyncClient,
    InviteMemberEvent,
    JoinError,
    JoinResponse,
    KeysQueryResponse,
    MegolmEvent,
    RoomMessageAudio,
    RoomMessageFile,
    SyncResponse,
    UnknownEncryptedEvent,
)
from nio.exceptions import EncryptionError, LocalProtocolError

from src.bot_service import BotService

logger = logging.getLogger(__name__)


def _own_mxid(client: AsyncClient) -> str:
    uid = (getattr(client, "user_id", "") or "").strip()
    if uid:
        return uid
    return (getattr(client, "user", "") or "").strip()


def register_matrix_callbacks(client: AsyncClient, service: BotService) -> None:
    first_sync_done = False

    def _trust_all_devices() -> None:
        # Auto-trust every device we learn about. The bot operates in untrusted
        # public rooms; refusing keys to unverified devices would block all decryption.
        own_user = client.user_id
        own_device = client.device_id
        for user_id, devices in client.device_store.items():
            for device_id, olm_device in devices.items():
                if user_id == own_user and device_id == own_device:
                    continue
                if olm_device.verified or olm_device.ignored:
                    continue
                client.verify_device(olm_device)

    async def _claim_missing_olm_sessions() -> None:
        # nio's sync_forever only auto-claims for "wedged" devices; a receive-only
        # bot must explicitly claim one-time keys to establish Olm sessions,
        # otherwise senders can't share Megolm room keys with us.
        to_claim: dict[str, list[str]] = defaultdict(list)
        for user_id, devices in client.device_store.items():
            if user_id == client.user_id:
                continue
            for device_id, olm_device in devices.items():
                if client.olm.session_store.get(olm_device.curve25519):
                    continue
                to_claim[user_id].append(device_id)
        if not to_claim:
            return
        logger.info("Claiming one-time keys for %d users", len(to_claim))
        try:
            await client.keys_claim(dict(to_claim))
            await client.send_to_device_messages()
        except Exception:
            logger.exception("keys_claim failed")

    async def on_first_sync(_response: SyncResponse) -> None:
        nonlocal first_sync_done
        if first_sync_done:
            return
        first_sync_done = True
        me = _own_mxid(client)
        for room_id in list(getattr(client, "invited_rooms", {}).keys()):
            if room_id in client.rooms:
                continue
            try:
                logger.info("Auto-joining residual invite: %s (mxid=%s)", room_id, me)
                resp = await client.join(room_id)
                if isinstance(resp, JoinError):
                    logger.error("Join failed: %s %s", room_id, resp.message)
            except Exception:
                logger.exception("Auto-join failed for %s", room_id)
        _trust_all_devices()
        await _claim_missing_olm_sessions()

    async def on_keys_query(_response: KeysQueryResponse) -> None:
        # Newly-discovered devices need to be trusted so we can establish Olm sessions
        _trust_all_devices()
        await _claim_missing_olm_sessions()

    async def on_encrypted(room: object, event: MegolmEvent) -> None:
        try:
            decrypted = await client.decrypt_event(event)
        except EncryptionError:
            logger.debug("Cannot decrypt event yet, requesting keys room=%s", room.room_id)  # type: ignore[attr-defined]
            # First make sure we have Olm sessions with the sender's devices,
            # then ask for the room key.
            await _claim_missing_olm_sessions()
            with contextlib.suppress(LocalProtocolError):
                await client.request_room_key(event)
            return
        if isinstance(decrypted, (RoomMessageAudio, RoomMessageFile)):
            try:
                await service.handle_audio_event(room, decrypted)
            except Exception:
                logger.exception("Error handling encrypted audio room=%s", room.room_id)  # type: ignore[attr-defined]

    async def on_invite(room: object, event: InviteMemberEvent) -> None:
        me = _own_mxid(client)
        if event.state_key != me:
            return
        room_id = room.room_id  # type: ignore[attr-defined]
        logger.info("Accepting invite to %s", room_id)
        resp = await client.join(room_id)
        if isinstance(resp, JoinError):
            logger.error("Join failed: %s %s", room_id, resp.message)
        elif isinstance(resp, JoinResponse):
            logger.info("Joined %s", room_id)

    async def on_audio(room: object, event: RoomMessageAudio) -> None:
        try:
            await service.handle_audio_event(room, event)
        except Exception:
            logger.exception("Error handling audio event room=%s", room.room_id)  # type: ignore[attr-defined]

    async def on_file(room: object, event: RoomMessageFile) -> None:
        info: dict = event.source.get("content", {}).get("info") or {}
        mime: str = info.get("mimetype", "")
        if not mime.startswith("audio/"):
            return
        try:
            await service.handle_audio_event(room, event)
        except Exception:
            logger.exception("Error handling file event room=%s", room.room_id)  # type: ignore[attr-defined]

    client.add_response_callback(on_first_sync, SyncResponse)
    client.add_response_callback(on_keys_query, KeysQueryResponse)
    client.add_event_callback(on_invite, InviteMemberEvent)
    client.add_event_callback(on_audio, RoomMessageAudio)
    client.add_event_callback(on_file, RoomMessageFile)
    client.add_event_callback(on_encrypted, MegolmEvent)
    client.add_event_callback(on_encrypted, UnknownEncryptedEvent)
