"""Private JSON-file/JSON-lines worker. stdout never contains document logs."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import sys
from pathlib import Path

from ..errors import InputError, StudioError
from .service import dispatch

MAX_REQUEST_BYTES = 4 * 1024 * 1024


def read_request(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REQUEST_BYTES:
            raise InputError("Worker request must be a regular file no larger than 4 MiB.")
        if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
            raise InputError("Worker request must be private to the current user (mode 0600).")
        data = stream.read(MAX_REQUEST_BYTES + 1)
    if len(data) > MAX_REQUEST_BYTES:
        raise InputError("Worker request exceeds 4 MiB.")
    try:
        request = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise InputError("Worker request is not valid UTF-8 JSON.") from None
    if not isinstance(request, dict):
        raise InputError("Worker request must be an object.")
    return request


def _limit_import() -> None:
    """Defence in depth; the GUI also enforces a 60 s wall-clock timeout."""
    try:
        import resource

        for resource_id, value in ((resource.RLIMIT_CPU, 45), (resource.RLIMIT_AS, 2 * 1024**3)):
            try:
                _, hard = resource.getrlimit(resource_id)
                limit = value if hard == resource.RLIM_INFINITY else min(value, hard)
                resource.setrlimit(resource_id, (limit, limit))
            except (OSError, ValueError):
                pass  # Some macOS runtimes don't enforce RLIMIT_AS; wall limit remains.
    except ImportError:
        pass


def emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False, allow_nan=False), flush=True)


async def _execute(request: dict) -> dict:
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()
    installed = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, current.cancel)
            installed.append(signum)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        return await dispatch(request, emit)
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 1:
            raise InputError("Pass one private JSON request file.")
        request = read_request(Path(argv[0]))
        if request.get("command") == "import":
            _limit_import()
        result = asyncio.run(_execute(request))
        emit({"event": "result", "command": request.get("command"), "data": result})
        return 0
    except StudioError as exc:
        emit({"event": "error", "code": exc.code, "message": str(exc)})
        return exc.exit_code
    except (KeyboardInterrupt, asyncio.CancelledError):
        emit({"event": "error", "code": "INTERRUPTED", "message": "Operation interrupted safely."})
        return 130
    except OSError:
        emit({"event": "error", "code": "LOCAL_IO_FAILED", "message": "Cannot access local files."})
        return 6
    except Exception:
        # Raw exceptions can contain user text, paths, upstream URLs or parser contents.
        emit(
            {
                "event": "error",
                "code": "DESKTOP_WORKER_ERROR",
                "message": "The operation failed. Check the selected input and local runtime.",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
