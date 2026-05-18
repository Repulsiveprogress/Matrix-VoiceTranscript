"""Patches for matrix-nio to support Matrix room version 11+.

In v11+, m.room.create no longer includes a `creator` field in content (MSC2175);
the author is in `sender`. nio 0.24 has a strict JSON schema and a from_dict
that KeyErrors on missing `creator`. This patch relaxes both.
"""

from __future__ import annotations

import copy
from typing import Any


def apply_nio_schema_patches() -> None:
    from nio import responses as nio_responses
    from nio.events import room_events
    from nio.schemas import Schemas

    rc_content = Schemas.room_create["properties"]["content"]
    req = list(rc_content.get("required", []))
    if "creator" in req:
        rc_content["required"] = [r for r in req if r != "creator"]

    _orig = room_events.RoomCreateEvent.from_dict.__func__

    @classmethod
    def _room_create_from_dict(
        cls: Any,
        parsed_dict: dict[str, Any],
    ) -> Any:
        pd = copy.deepcopy(parsed_dict)
        content = pd.setdefault("content", {})
        if "creator" not in content:
            content["creator"] = pd.get("sender") or ""
        if "m.federate" not in content:
            content["m.federate"] = True
        if "room_version" not in content:
            content["room_version"] = "1"
        return _orig(cls, pd)

    room_events.RoomCreateEvent.from_dict = _room_create_from_dict

    # Synapse/Dendrite omit `one_time_key_counts` from /keys/upload when empty,
    # but nio's schema marks it required and from_dict does an unguarded lookup.
    # https://github.com/matrix-nio/matrix-nio/issues/510
    ku = Schemas.keys_upload
    ku_req = list(ku.get("required", []))
    if "one_time_key_counts" in ku_req:
        ku["required"] = [r for r in ku_req if r != "one_time_key_counts"]

    _orig_ku = nio_responses.KeysUploadResponse.from_dict.__func__

    @classmethod
    def _ku_from_dict(cls: Any, parsed_dict: dict[str, Any]) -> Any:
        pd = copy.deepcopy(parsed_dict)
        counts = pd.setdefault("one_time_key_counts", {})
        counts.setdefault("curve25519", 0)
        counts.setdefault("signed_curve25519", 0)
        return _orig_ku(cls, pd)

    nio_responses.KeysUploadResponse.from_dict = _ku_from_dict
