"""One long-lived CPU Piper process (backend.app.voice.piper_worker) so each answer does not reload the voice (~5 s).

Every doubt ends the same way: the process is killed and PiperWorkerError is raised, so VoiceService falls back to the
per-request worker (the method used before) for that answer and pauses the persistent worker for a while. Calls are
serialized by a lock (VoiceService also keeps its single "busy" operation, as before).
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

START_TIMEOUT_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 45
MAX_LINE_BYTES = 64 * 1024


class PiperWorkerError(RuntimeError):
    """The persistent worker failed, hung or answered badly; the caller falls back. Carries a short code only."""


class _Process:
    """Blocking line I/O with a child process; only called from worker threads (asyncio.to_thread)."""

    def __init__(self, popen):
        self.popen = popen

    @property
    def alive(self):
        return self.popen.poll() is None

    def read_line(self):
        line = self.popen.stdout.readline(MAX_LINE_BYTES)
        if not line:
            raise PiperWorkerError("crashed")
        return line

    def request(self, line):
        self.popen.stdin.write(line + "\n")
        self.popen.stdin.flush()
        return self.read_line()

    def terminate(self):
        try:
            self.popen.kill()
        except OSError:
            pass
        try:
            self.popen.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def start_process(models, temporary, root):
    """Default starter: the child on CPU (CUDA hidden), 2 threads, line-buffered UTF-8 pipes, no console window."""
    environment = {**os.environ, "TEMP": str(temporary), "TMP": str(temporary), "OMP_NUM_THREADS": "2",
                   "OPENBLAS_NUM_THREADS": "2", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                   "CUDA_VISIBLE_DEVICES": "-1"}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    popen = subprocess.Popen([sys.executable, "-B", "-m", "backend.app.voice.piper_worker", str(models)],
                             cwd=root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                             creationflags=flags)
    return _Process(popen)


class PersistentPiper:
    def __init__(self, models, temporary, root, start=start_process,
                 start_timeout=START_TIMEOUT_SECONDS, request_timeout=REQUEST_TIMEOUT_SECONDS):
        self.models, self.temporary, self.root = Path(models), Path(temporary), Path(root)
        self._start = start
        self.start_timeout, self.request_timeout = start_timeout, request_timeout
        self._process = None
        self._lock = asyncio.Lock()

    @property
    def loaded(self):
        return self._process is not None and self._process.alive

    async def ensure_started(self):
        """Start the child and wait for its "ready" line (first use or preload)."""
        async with self._lock:
            await self._ensure()

    async def _ensure(self):
        if self.loaded:
            return self._process
        await self._kill()
        try:
            process = await asyncio.to_thread(self._start, self.models, self.temporary, self.root)
        except Exception as exc:  # noqa: BLE001
            raise PiperWorkerError("spawn_failed") from exc
        self._process = process
        reply = await self._read(process.read_line, self.start_timeout, "start_timeout")
        if reply.get("ready") is not True:
            await self._kill()
            raise PiperWorkerError("startup_failed")
        logger.info("Piper persistent worker ready load_seconds=%s providers=%s", reply.get("load_seconds"),
                    ",".join(str(p) for p in reply.get("providers") or [])[:80])
        return process

    async def synthesize(self, task):
        """Write task["output"] (text, speaker, speed as the per-request worker). Returns the worker's numbers."""
        async with self._lock:
            process = await self._ensure()
            line = json.dumps({key: task[key] for key in ("text", "speaker", "speed", "output")})
            reply = await self._read(lambda: process.request(line), self.request_timeout, "request_timeout")
            if reply.get("ok") is not True:
                # One failed synthesis: the process is replaced next time; this answer falls back.
                await self._kill()
                raise PiperWorkerError("synthesis_failed")
            return reply

    async def _read(self, call, timeout, code):
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(call), timeout)
        except asyncio.TimeoutError as exc:
            await self._kill()
            raise PiperWorkerError(code) from exc
        except PiperWorkerError:
            await self._kill()
            raise
        except (OSError, ValueError) as exc:
            await self._kill()
            raise PiperWorkerError("pipe_error") from exc
        try:
            reply = json.loads(raw)
        except ValueError as exc:
            await self._kill()
            raise PiperWorkerError("protocol_error") from exc
        if not isinstance(reply, dict):
            await self._kill()
            raise PiperWorkerError("protocol_error")
        return reply

    async def _kill(self):
        process, self._process = self._process, None
        if process is not None:
            await asyncio.to_thread(process.terminate)

    async def stop(self):
        async with self._lock:
            await self._kill()
