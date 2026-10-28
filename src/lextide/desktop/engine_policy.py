"""Text-free Auto selection; reachability is never a synthesis guarantee.

This module neither maps voices/rates between engines nor retries a failed
synthesis with another engine. Selection completes before the user starts a run.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Mapping

import aiohttp
import certifi
import edge_tts

from ..errors import InputError

PROBE_TIMEOUT_SECONDS = 4.0
_REACHABILITY_NOTICE = (
    "The Edge endpoint responded to a text-free connectivity check. "
    "Synthesis may still fail; Generate authorizes sending the reviewed text online."
)


def _decision(engine: str | None, ready: bool, code: str, reason: str, *, probe=False) -> dict:
    return {
        "engine": engine,
        "actual_engine": engine,
        "ready": ready,
        "reason": reason,
        "reason_code": code,
        "probe_required": probe,
    }


def resolve_engine(
    selected_engine: str,
    status: Mapping | None,
    edge_probe: Mapping | None = None,
) -> dict:
    """Choose from existing discovery evidence without I/O or settings changes.

    ``status`` is the complete result of ``service.engines`` (whose model check
    verifies hashes). A missing Auto probe is pending, not a failed probe. The
    returned ``ready`` only describes engine prerequisites, never voice/rate
    compatibility, document review or permission to send text.
    """
    if selected_engine not in {"auto", "edge-tts", "kokoro"}:
        raise InputError("Choose Auto, Edge or Kokoro.")
    if status is None:
        return _decision(
            None if selected_engine == "auto" else selected_engine,
            False,
            "discovery_pending",
            "Checking local engine requirements.",
        )
    capabilities = {
        item.get("engine_id"): item
        for item in status.get("engines", [])
        if isinstance(item, Mapping)
    }
    edge_ready = capabilities.get("edge-tts", {}).get("available") is True
    kokoro_ready = (
        capabilities.get("kokoro", {}).get("available") is True
        and status.get("offline_runtime", {}).get("ready") is True
        and status.get("model", {}).get("ready") is True
    )
    # Availability already incorporates media tools in service.engines. If the
    # evidence explicitly reports a missing tool, remain conservative even when
    # a stale/inconsistent capability accidentally says available.
    media = status.get("media_tools", {})
    if any(tool.get("available") is not True for tool in media.values()):
        edge_ready = kokoro_ready = False

    if selected_engine == "edge-tts":
        return _decision(
            "edge-tts",
            edge_ready,
            "explicit_edge" if edge_ready else "edge_not_ready",
            (
                "Edge was selected explicitly. Generate authorizes sending the reviewed text "
                "online; service availability is checked during synthesis."
                if edge_ready
                else "Edge local requirements are not ready. Check the media tools."
            ),
        )
    if selected_engine == "kokoro":
        return _decision(
            "kokoro",
            kokoro_ready,
            "explicit_kokoro" if kokoro_ready else "kokoro_not_ready",
            (
                "Kokoro was selected explicitly. Synthesis stays on this computer."
                if kokoro_ready
                else "Kokoro is not ready. Check its local runtime, verified models "
                "and media tools."
            ),
        )
    if edge_ready and edge_probe is None:
        return _decision(
            None,
            False,
            "probe_pending",
            "Auto is checking the Edge endpoint without sending document text.",
            probe=True,
        )
    if edge_ready and edge_probe.get("reachable") is True:
        return _decision("edge-tts", True, "auto_edge", _REACHABILITY_NOTICE)
    if kokoro_ready:
        return _decision(
            "kokoro",
            True,
            "auto_kokoro",
            "Auto selected the ready local Kokoro engine because Edge connectivity or its local "
            "requirements could not be confirmed. Check the displayed voice and rate "
            "before Generate.",
        )
    return _decision(
        None,
        False,
        "no_engine_ready",
        "Auto could not confirm Edge connectivity and no ready local Kokoro engine is available. "
        "Retry the check, select Edge explicitly, or prepare Kokoro locally.",
    )


def _probe_result(reachable: bool, code: str, reason: str, status: int | None = None) -> dict:
    return {
        "reachable": reachable,
        "reason": reason,
        "reason_code": code,
        "http_status": status,
        "scope": "reachability-only",
        "probe_method": "edge_tts.list_voices",
        "network_policy": "upstream-proxy-and-redirect-defaults",
    }


async def probe_edge_reachability() -> dict:
    """Use the installed upstream's public API with no input/text arguments.

    The voice catalogue request contains no synthesis text. Upstream owns its
    endpoint/protocol, redirects and proxy environment handling (trust_env=True
    in the pinned version). We supply a verified TLS connector and impose one
    overall deadline, including upstream retries. No model download or TTS call
    is made. Catalogue contents, URLs, headers and proxy settings are not exposed.
    """
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            async with aiohttp.TCPConnector(ssl=context) as connector:
                voices = await edge_tts.list_voices(connector=connector)
        if (
            isinstance(voices, list)
            and voices
            and all(
                isinstance(voice, dict)
                and isinstance(voice.get("ShortName"), str)
                and voice["ShortName"].strip()
                for voice in voices
            )
        ):
            # The public API does not expose a success HTTP status; do not invent it.
            return _probe_result(True, "reachable", _REACHABILITY_NOTICE)
        return _probe_result(
            False,
            "invalid_catalog",
            "The text-free Edge connectivity check did not return a valid voice catalogue.",
        )
    except TimeoutError:
        return _probe_result(False, "timeout", "The text-free Edge connectivity check timed out.")
    except aiohttp.ClientResponseError as exc:
        return _probe_result(
            False,
            "http_error",
            f"The text-free Edge connectivity check returned HTTP {exc.status}.",
            exc.status,
        )
    except (aiohttp.ClientError, OSError):
        # Raw upstream exceptions may contain URLs or network configuration.
        return _probe_result(
            False,
            "connection_failed",
            "The text-free Edge connectivity check could not establish a verified "
            "HTTPS connection.",
        )
    except (KeyError, TypeError, ValueError):
        return _probe_result(
            False,
            "invalid_catalog",
            "The text-free Edge connectivity check did not return a valid voice catalogue.",
        )
