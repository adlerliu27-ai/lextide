"""G3 process lifetime guard: parent EOF kills and reaps the entire child session.

The supervisor loads no model. It polls a private parent pipe while the accepted
worker runs in its own process group, so SIGKILL of the CLI cannot leave a native
inference call running indefinitely. This is a lifetime guard, not a sandbox.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import select
import signal
import subprocess
import sys

from ..runtime import is_frozen


async def run_supervised(arguments: list[str], *, env: dict | None = None) -> int:
    """Run a private command and wait for cleanup on cancellation.

    stdin is a liveness pipe only; no input text or output travels through it.
    The supervisor uses the caller's Python and standard library, while the
    command retains its chosen executable (including the isolated Kokoro venv).
    """
    prefix = ["--supervise"] if is_frozen() else ["-B", "-m", "lextide.longform.process"]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *prefix,
        str(os.getpid()),
        *arguments,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        return await process.wait()
    except BaseException:
        # Do not immediately SIGKILL the supervisor: it must reap its child.
        if process.stdin is not None:
            process.stdin.close()
        try:
            async with asyncio.timeout(5):
                await asyncio.shield(process.wait())
        except (TimeoutError, asyncio.CancelledError):
            # SIGTERM is handled by the supervisor as another cleanup request.
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            await asyncio.shield(process.wait())
        raise
    finally:
        if process.stdin is not None:
            process.stdin.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        return 2
    try:
        parent_pid = int(args[0])
    except ValueError:
        return 2
    if parent_pid <= 1 or os.getppid() != parent_pid:
        return 2
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        child = subprocess.Popen(
            args[1:],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return 2
    try:
        while child.poll() is None:
            if stopping or os.getppid() != parent_pid:
                break
            readable, _, _ = select.select([sys.stdin.fileno()], [], [], 0.05)
            if readable and not os.read(sys.stdin.fileno(), 1):
                break
        if child.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
        return child.wait()
    finally:
        # Also stop descendants if the direct command exited before its children.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
