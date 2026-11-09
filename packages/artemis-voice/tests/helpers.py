"""Shared test doubles."""

from __future__ import annotations

import array
import asyncio
import math
from types import SimpleNamespace

from artemis_client.models import TaskResult
from artemis_voice.stt.base import Transcript


def tone(ms: int, amplitude: int = 8000, rate: int = 16000, freq: int = 440) -> bytes:
    n = rate * ms // 1000
    return array.array(
        "h", (int(amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))
    ).tobytes()


def silence(ms: int, rate: int = 16000) -> bytes:
    return bytes(rate * ms // 1000 * 2)


class FakeClient:
    """Stands in for ArtemisClient: records calls, finishes tasks on demand."""

    def __init__(self, status: str = "completed", output: str = "ok", hold: bool = False) -> None:
        self.status, self.output, self.hold = status, output, hold
        self.submitted: list[dict] = []
        self.stopped: list[str] = []
        self.release = asyncio.Event()
        self.fail_submit: Exception | None = None

    async def submit(self, goal, **kwargs):
        if self.fail_submit:
            raise self.fail_submit
        self.submitted.append({"goal": goal, **kwargs})
        return SimpleNamespace(task_id=f"task-{len(self.submitted)}", status="queued")

    async def wait_for_task(self, task_id, timeout=0):
        if self.hold:
            await self.release.wait()
        status = "cancelled" if self.stopped else self.status
        return TaskResult(task_id=task_id, status=status, output=self.output)

    async def get_task(self, task_id):
        done = not self.hold or self.release.is_set()
        return TaskResult(task_id=task_id, status="completed" if done else "running")

    async def stop(self, task_id):
        self.stopped.append(task_id)
        self.release.set()
        return True


class ScriptedSTT:
    name = "scripted"

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)

    def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        return Transcript(text=self.texts.pop(0) if self.texts else "")


class Collect:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.spoken: list[str | None] = []
        self.interrupts = 0

    def say(self, message: str, spoken: str | None = None) -> None:
        self.messages.append(message)
        self.spoken.append(spoken)

    def interrupt(self) -> None:
        self.interrupts += 1

    def has(self, fragment: str) -> bool:
        return any(fragment in m for m in self.messages)
