"""G8 engine selection is deterministic; endpoint I/O is always mocked."""

import asyncio
import copy
import ssl

import aiohttp
import pytest

from lextide.desktop import engine_policy as policy
from lextide.desktop import service
from lextide.errors import InputError


def discovery(*, edge=True, kokoro=True, runtime=True, model=True, media=True):
    return {
        "engines": [
            {"engine_id": "edge-tts", "available": edge, "voices": ["en-US-GuyNeural"]},
            {"engine_id": "kokoro", "available": kokoro, "voices": ["af_heart"]},
        ],
        "offline_runtime": {"ready": runtime},
        "model": {"ready": model},
        "media_tools": {
            "ffmpeg": {"available": media},
            "ffprobe": {"available": media},
        },
    }


@pytest.mark.parametrize("mode", ["auto", "edge-tts", "kokoro"])
def test_discovery_pending_never_advertises_ready(mode):
    decision = policy.resolve_engine(mode, None)
    assert decision["ready"] is False
    assert decision["reason_code"] == "discovery_pending"


def test_auto_waits_for_probe_even_when_kokoro_is_ready():
    result = policy.resolve_engine("auto", discovery())
    assert result["actual_engine"] is None
    assert result["engine"] is None
    assert result["ready"] is False
    assert result["probe_required"] is True
    assert result["reason_code"] == "probe_pending"


@pytest.mark.parametrize("kokoro", [True, False])
def test_auto_prefers_reachable_edge(kokoro):
    result = policy.resolve_engine("auto", discovery(kokoro=kokoro), {"reachable": True})
    assert result["actual_engine"] == "edge-tts"
    assert result["ready"] is True
    assert result["probe_required"] is False
    assert "Synthesis may still fail" in result["reason"]


def test_auto_selects_kokoro_only_after_failed_probe():
    result = policy.resolve_engine("auto", discovery(), {"reachable": False})
    assert result["actual_engine"] == "kokoro"
    assert result["ready"] is True
    assert result["reason_code"] == "auto_kokoro"
    assert "voice and rate" in result["reason"]


@pytest.mark.parametrize("missing", ["kokoro", "runtime", "model", "media"])
def test_failed_edge_probe_cannot_bypass_any_local_prerequisite(missing):
    result = policy.resolve_engine("auto", discovery(**{missing: False}), {"reachable": False})
    assert result["actual_engine"] is None
    assert result["ready"] is False
    assert result["reason_code"] == "no_engine_ready"


def test_auto_can_use_ready_kokoro_when_edge_local_requirements_are_unavailable():
    result = policy.resolve_engine("auto", discovery(edge=False))
    assert result["actual_engine"] == "kokoro"
    assert result["ready"] is True
    assert result["probe_required"] is False


def test_reachable_probe_never_overrides_missing_edge_requirements():
    result = policy.resolve_engine("auto", discovery(media=False), {"reachable": True})
    assert result["actual_engine"] is None
    assert result["ready"] is False


@pytest.mark.parametrize("mode", ["edge-tts", "kokoro"])
@pytest.mark.parametrize("reachable", [None, False, True])
def test_explicit_selection_ignores_probe_and_never_switches_engines(mode, reachable):
    probe = None if reachable is None else {"reachable": reachable}
    result = policy.resolve_engine(mode, discovery(), probe)
    assert result["actual_engine"] == mode
    assert result["ready"] is True
    assert result["probe_required"] is False
    if mode == "edge-tts":
        assert "Generate authorizes" in result["reason"]


@pytest.mark.parametrize("mode", ["edge-tts", "kokoro"])
def test_explicit_unavailable_engine_stays_selected(mode):
    status = discovery(edge=False, kokoro=False)
    result = policy.resolve_engine(mode, status, {"reachable": True})
    assert result["actual_engine"] == mode
    assert result["ready"] is False
    assert result["probe_required"] is False


@pytest.mark.parametrize("mode", ["", "automatic", "unknown"])
def test_rejects_unknown_mode(mode):
    with pytest.raises(InputError):
        policy.resolve_engine(mode, discovery())


def test_policy_never_mutates_discovery_or_invents_voice_rate():
    status = discovery()
    previous = copy.deepcopy(status)
    result = policy.resolve_engine("auto", status, {"reachable": False})
    assert status == previous
    assert "voice" not in result and "rate" not in result and "settings" not in result


def mock_voice_api(monkeypatch, result):
    calls = []

    async def list_voices(**kwargs):
        calls.append(kwargs)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(policy.edge_tts, "list_voices", list_voices)
    return calls


def test_probe_uses_public_voice_api_and_verified_tls_without_document_arguments(monkeypatch):
    calls = mock_voice_api(monkeypatch, [{"ShortName": "en-US-GuyNeural"}])
    contexts = []
    original = policy.aiohttp.TCPConnector

    def connector(**kwargs):
        contexts.append(kwargs["ssl"])
        return original(**kwargs)

    monkeypatch.setattr(policy.aiohttp, "TCPConnector", connector)
    result = asyncio.run(policy.probe_edge_reachability())
    assert result["reachable"] is True
    assert result["scope"] == "reachability-only"
    assert result["probe_method"] == "edge_tts.list_voices"
    assert result["network_policy"] == "upstream-proxy-and-redirect-defaults"
    assert "Synthesis may still fail" in result["reason"]
    assert len(calls) == 1 and set(calls[0]) == {"connector"}
    assert calls[0]["connector"].closed is True
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert contexts[0].check_hostname is True
    assert policy.PROBE_TIMEOUT_SECONDS == 4.0
    assert result["http_status"] is None
    assert "voices" not in result and "url" not in result and "headers" not in result
    assert "en-US-GuyNeural" not in str(result)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_probe_retains_http_failure_status_without_raw_details(monkeypatch, status):
    error = aiohttp.ClientResponseError(None, (), status=status, message="private details")
    calls = mock_voice_api(monkeypatch, error)
    result = asyncio.run(policy.probe_edge_reachability())
    assert result["reachable"] is False
    assert result["reason_code"] == "http_error"
    assert result["http_status"] == status
    assert len(calls) == 1
    assert "private details" not in str(result)


@pytest.mark.parametrize(
    "catalog",
    [[], None, {}, [{"ShortName": ""}], [{"ShortName": 3}], ["private metadata"]],
)
def test_probe_rejects_empty_or_malformed_catalogue(monkeypatch, catalog):
    mock_voice_api(monkeypatch, catalog)
    result = asyncio.run(policy.probe_edge_reachability())
    assert result["reachable"] is False
    assert result["reason_code"] == "invalid_catalog"
    assert "private metadata" not in str(result)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TimeoutError("private details"), "timeout"),
        (aiohttp.ClientConnectionError("private details"), "connection_failed"),
        (ssl.SSLError("private details"), "connection_failed"),
        (OSError("private details"), "connection_failed"),
        (ValueError("private details"), "invalid_catalog"),
        (TypeError("private details"), "invalid_catalog"),
        (KeyError("private details"), "invalid_catalog"),
    ],
)
def test_probe_sanitizes_connection_and_parse_failures(monkeypatch, error, code):
    mock_voice_api(monkeypatch, error)
    result = asyncio.run(policy.probe_edge_reachability())
    assert result["reachable"] is False
    assert result["reason_code"] == code
    assert "private details" not in str(result)


def test_probe_deadline_bounds_the_public_api_call(monkeypatch):
    cancelled = []

    async def delayed(**_kwargs):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(policy, "PROBE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(policy.edge_tts, "list_voices", delayed)
    result = asyncio.run(policy.probe_edge_reachability())
    assert result["reason_code"] == "timeout"
    assert cancelled == [True]


def test_probe_cancellation_is_not_a_failed_connectivity_result(monkeypatch):
    mock_voice_api(monkeypatch, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(policy.probe_edge_reachability())


def test_auto_probe_dispatch_reuses_verified_discovery_and_returns_evidence(monkeypatch):
    requests, probes = [], []
    local_status = discovery()

    def local_discovery(request):
        requests.append(request)
        return local_status

    async def connectivity():
        probes.append(True)
        return {"reachable": False, "reason_code": "timeout", "scope": "reachability-only"}

    monkeypatch.setattr(service, "engines", local_discovery)
    monkeypatch.setattr(policy, "probe_edge_reachability", connectivity)
    request = {"command": "auto_probe", "runtime": {"model_dir": "/models"}}
    result = asyncio.run(service.dispatch(request))
    assert requests == [request]
    assert probes == [True]
    assert result["engines"] == local_status["engines"]
    assert result["decision"]["actual_engine"] == "kokoro"
    assert result["ready"] is True
    assert result["edge_probe"]["reason_code"] == "timeout"


def test_auto_probe_skips_network_when_no_local_edge_requirements(monkeypatch):
    monkeypatch.setattr(service, "engines", lambda _request: discovery(edge=False))

    async def forbidden():
        pytest.fail("Edge probe is unnecessary")

    monkeypatch.setattr(policy, "probe_edge_reachability", forbidden)
    result = asyncio.run(service.dispatch({"command": "auto_probe"}))
    assert result["actual_engine"] == "kokoro"
    assert result["edge_probe"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"text": "PRIVATE DOCUMENT"},
        {"settings": {"voice": "PRIVATE DOCUMENT"}},
        {"url": "https://untrusted.example"},
        {"runtime": {"text": "PRIVATE DOCUMENT"}},
        {"runtime": "PRIVATE DOCUMENT"},
    ],
)
def test_auto_probe_rejects_text_and_nonruntime_fields_before_any_io(monkeypatch, extra):
    def forbidden(_request):
        pytest.fail("Invalid probe requests must be rejected before discovery or network I/O")

    monkeypatch.setattr(service, "engines", forbidden)
    with pytest.raises(InputError) as error:
        asyncio.run(service.dispatch({"command": "auto_probe", **extra}))
    assert "PRIVATE DOCUMENT" not in str(error.value)


def test_explicit_kokoro_discovery_never_calls_connectivity(monkeypatch):
    monkeypatch.setattr(service, "engines", lambda _request: discovery())

    async def forbidden():
        pytest.fail("Explicit Kokoro must not contact Edge")

    monkeypatch.setattr(policy, "probe_edge_reachability", forbidden)
    status = asyncio.run(service.dispatch({"command": "engines"}))
    assert policy.resolve_engine("kokoro", status)["ready"] is True
