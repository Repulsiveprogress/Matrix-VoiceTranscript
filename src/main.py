from __future__ import annotations

import asyncio
import logging
import os
import sys

from nio import (
    AsyncClient,
    AsyncClientConfig,
    DeleteDevicesError,
    DeleteDevicesResponse,
    DevicesError,
    WhoamiError,
)
from nio.responses import DeleteDevicesAuthResponse

from src.bot_service import BotService
from src.config import Settings
from src.matrix_handlers import register_matrix_callbacks
from src.nio_patch import apply_nio_schema_patches
from src.strings import make_strings
from src.transcriber import Transcriber

_PRIVACY_SENSITIVE_KEYS = frozenset({"body", "text", "transcript"})


class _PrivacyFilter(logging.Filter):
    """Drops log records that accidentally include user message content."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, dict) and _PRIVACY_SENSITIVE_KEYS.intersection(args):
            record.msg = "[PRIVACY FILTERED] log record contained sensitive keys"
            record.args = ()
        return True


async def _prune_other_devices(client: AsyncClient, password: str) -> None:
    """Delete every device on the bot account except the current one.

    Required for E2EE hygiene: senders try to share Megolm keys with every
    known device of the recipient; stale devices wedge decryption.
    """
    log = logging.getLogger(__name__)
    resp = await client.devices()
    if isinstance(resp, DevicesError):
        log.warning("devices() failed: %s", resp.message)
        return

    keep = client.device_id
    stale = [d.id for d in resp.devices if d.id != keep]
    if not stale:
        log.info("No stale devices to prune (current=%s)", keep)
        return

    log.info("Pruning %d stale device(s), keeping %s", len(stale), keep)
    first = await client.delete_devices(stale)
    if isinstance(first, DeleteDevicesResponse):
        log.info("Pruned without UIA")
        return
    if isinstance(first, DeleteDevicesError):
        log.error("delete_devices failed: %s", first.message)
        return
    if not isinstance(first, DeleteDevicesAuthResponse):
        log.error("Unexpected delete_devices response: %r", first)
        return

    auth = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": client.user_id},
        "user": client.user_id,
        "password": password,
        "session": first.session,
    }
    second = await client.delete_devices(stale, auth=auth)
    if isinstance(second, DeleteDevicesResponse):
        log.info("Pruned %d stale device(s)", len(stale))
    else:
        log.error("delete_devices UIA step failed: %r", second)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger().addFilter(_PrivacyFilter())
    for noisy in ("nv_one_logger", "nemo_logger", "matplotlib.font_manager", "numexpr.utils"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


async def async_main() -> None:
    apply_nio_schema_patches()

    settings = Settings()
    strings = make_strings(settings.locale)

    # Load ASR model before connecting; startup takes 10-90s depending on cache
    transcriber = Transcriber.load(settings.asr_model_name)

    os.makedirs(settings.store_path, exist_ok=True)

    log = logging.getLogger(__name__)

    # Discover the real device_id bound to the access token; using a different
    # device_id breaks E2EE because the Olm store is keyed on it.
    probe = AsyncClient(settings.matrix_homeserver_base(), user=settings.matrix_user_id)
    probe.access_token = settings.matrix_access_token
    probe.user_id = settings.matrix_user_id
    who = await probe.whoami()
    await probe.close()
    if isinstance(who, WhoamiError) or not who.device_id:
        raise RuntimeError(f"whoami failed: {who}")
    real_device_id = who.device_id
    log.info("Resolved device_id from token: %s", real_device_id)

    config = AsyncClientConfig(
        encryption_enabled=True,
        store_sync_tokens=True,
    )
    matrix = AsyncClient(
        settings.matrix_homeserver_base(),
        user=settings.matrix_user_id,
        device_id=real_device_id,
        store_path=settings.store_path,
        config=config,
    )
    matrix.restore_login(
        settings.matrix_user_id,
        real_device_id,
        settings.matrix_access_token,
    )
    matrix.load_store()

    # Push current Olm identity keys to the server so peers refetch them via
    # device_lists.changed. Critical after recreating the Olm store under the
    # same device_id, without this senders keep encrypting to stale keys.
    if matrix.should_upload_keys:
        try:
            await matrix.keys_upload()
        except Exception:
            log.exception("Initial keys_upload failed")

    # Remove stale device records left by previous bot sessions / other clients.
    # Otherwise Element shares Megolm keys with devices we have no Olm keys for.
    if settings.matrix_password:
        try:
            await _prune_other_devices(matrix, settings.matrix_password)
        except Exception:
            log.exception("Device pruning failed")
    else:
        log.warning(
            "MATRIX_PASSWORD not set, stale devices will not be pruned, "
            "E2EE decryption may fail. Set the bot password in .env to enable.",
        )

    service = BotService(settings, matrix, transcriber, strings)
    register_matrix_callbacks(matrix, service)

    log.info("Starting Matrix sync (user=%s)", settings.matrix_user_id)

    try:
        await matrix.sync_forever(timeout=30000, full_state=True)
    finally:
        await matrix.close()


def main() -> None:
    _configure_logging()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
