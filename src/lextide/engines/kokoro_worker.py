"""Private subprocess entry point. Optional inference packages never enter the CLI process."""

from __future__ import annotations

import json
import math
import os
import socket
import sys
import wave
from importlib import metadata, util
from pathlib import Path

_MAX_SECONDS = 600
_MAX_REQUEST_BYTES = 128 * 1024
_RUNTIME_PACKAGES = {
    "kokoro-onnx": ("kokoro_onnx", "0.6.1"),
    "onnxruntime": ("onnxruntime", "1.31.0"),
    "numpy": ("numpy", "2.5.4"),
    "phonemizer": ("phonemizer", "3.4.0"),
    "espeakng-loader": ("espeakng_loader", "0.2.4"),
}


def deny_network() -> None:
    """Deny Python IP sockets and DNS before importing the optional runtime.

    This process guard complements offline flags; it is not an OS network sandbox
    for arbitrary native code or an executable supplied by the user.
    """
    original_socket = socket.socket

    def denied(*args, **kwargs):
        raise PermissionError("Offline worker network access is disabled")

    class OfflineSocket(original_socket):
        def __init__(self, family=socket.AF_INET, *args, **kwargs):
            if family in (socket.AF_INET, socket.AF_INET6):
                denied()
            super().__init__(family, *args, **kwargs)

    socket.socket = OfflineSocket
    for name in (
        "create_connection",
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
        "gethostbyaddr",
        "getnameinfo",
    ):
        setattr(socket, name, denied)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[name] = "1"
    # Use the pinned wheel's bundled eSpeak resources, not user-supplied overrides.
    for name in ("PHONEMIZER_ESPEAK_LIBRARY", "ESPEAK_DATA_PATH"):
        os.environ.pop(name, None)


def runtime_status() -> dict:
    """Only inspect metadata/module locations. No model load or optional import."""
    versions = {}
    try:
        if sys.version_info[:2] != (3, 13):
            return {"ready": False, "reason": "python_version"}
        for distribution, (module, expected_version) in _RUNTIME_PACKAGES.items():
            versions[distribution] = metadata.version(distribution)
            if util.find_spec(module) is None:
                return {"ready": False, "reason": "dependency_missing"}
            if versions[distribution] != expected_version:
                return {"ready": False, "reason": "runtime_version"}
    except (metadata.PackageNotFoundError, ImportError, ValueError):
        return {"ready": False, "reason": "dependency_missing"}
    return {"ready": True, "versions": versions}


def _synthesize(request: dict, directory: Path) -> dict:
    import numpy as np
    from kokoro_onnx import Kokoro

    sentences = request["sentences"]
    speed = request["speed"]
    voice = request["voice"]
    lang = request["lang"]
    if (
        not isinstance(sentences, list)
        or not 1 <= len(sentences) <= 1000
        or not all(isinstance(s, str) and s.strip() for s in sentences)
        or not isinstance(speed, (float, int))
        or not math.isfinite(speed)
        or not 0.5 <= speed <= 2.0
        or lang not in {"en-us", "en-gb"}
        or not isinstance(voice, str)
        or not voice.startswith(("af_", "am_", "bf_", "bm_"))
    ):
        return {"ok": False, "code": "OFFLINE_REQUEST_INVALID"}
    try:
        engine = Kokoro(request["model_path"], request["voices_path"])
    except Exception:
        return {"ok": False, "code": "OFFLINE_MODEL_LOAD_FAILED"}
    if voice not in engine.get_voices():
        return {"ok": False, "code": "OFFLINE_VOICE_UNAVAILABLE"}
    counts = []
    sample_rate = None
    total = 0
    with wave.open(str(directory / "audio.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)  # Keep even error-path WAV closure well-defined.
        for sentence in sentences:
            samples, rate = engine.create(
                sentence,
                voice=voice,
                speed=speed,
                lang=lang,
                trim=True,
                sentence_pause=0.25,
                clause_pause=0.1,
                continuous=False,
            )
            samples = np.asarray(samples)
            if (
                type(rate) is not int
                or not 8000 <= rate <= 48000
                or samples.ndim != 1
                or samples.size == 0
                or not np.issubdtype(samples.dtype, np.floating)
                or not np.isfinite(samples).all()
                or (sample_rate is not None and sample_rate != rate)
                or total + samples.size > rate * _MAX_SECONDS
            ):
                return {"ok": False, "code": "OFFLINE_AUDIO_INVALID"}
            if sample_rate is None:
                sample_rate = rate
                output.setframerate(rate)
            # Quantization does not add/drop samples. There is no intermediate MP3.
            pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
            output.writeframesraw(pcm.tobytes())
            counts.append(int(samples.size))
            total += int(samples.size)
    return {"ok": True, "sample_rate": sample_rate, "sample_counts": counts, "total_samples": total}


def main(argv: list[str] | None = None) -> int:
    deny_network()
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--check"]:
        print(json.dumps(runtime_status(), separators=(",", ":")))
        return 0
    if len(arguments) != 1:
        return 2
    request_path = Path(arguments[0])
    result_path = request_path.parent / "result.json"
    result = {"ok": False, "code": "OFFLINE_SYNTHESIS_FAILED"}
    try:
        if request_path.stat().st_size > _MAX_REQUEST_BYTES:
            result = {"ok": False, "code": "OFFLINE_REQUEST_INVALID"}
        elif not runtime_status()["ready"]:
            result = {"ok": False, "code": "OFFLINE_DEPENDENCY_MISSING"}
        else:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            result = _synthesize(request, request_path.parent)
    except (ImportError, ModuleNotFoundError):
        result = {"ok": False, "code": "OFFLINE_DEPENDENCY_MISSING"}
    except OSError:
        result = {"ok": False, "code": "OFFLINE_IO_FAILED"}
    except Exception:
        # Neither exception strings nor tracebacks cross the private worker boundary.
        pass
    try:
        with result_path.open("x", encoding="utf-8") as output:
            json.dump(result, output, separators=(",", ":"))
    except OSError:
        return 2
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
