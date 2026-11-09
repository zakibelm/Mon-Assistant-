# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The voice assistant: listen -> understand -> (confirm) -> run on the phone."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from artemis_client.errors import ArtemisClientError, TaskTimeoutError

from artemis_voice.config import VoiceConfig
from artemis_voice.errors import SpeechRecognitionError
from artemis_voice.intent import Intent, IntentKind, parse_confirmation, parse_intent
from artemis_voice.listener import Listener

MSG = {
    "busy": "Une tâche est déjà en cours. Dis « stop » pour l'arrêter.",
    "idle": "Aucune tâche en cours.",
    "stopped": "Tâche arrêtée.",
    "stop_requested": "Arrêt demandé.",
    "cancelled": "Annulé.",
    "bye": "À bientôt.",
    "unclear": "Je n'ai pas compris. Réponds par oui ou par non.",
}


class Responder(Protocol):
    def say(self, message: str, spoken: str | None = None) -> None:
        """Show ``message``; ``spoken`` is an optional shorter text for a voice."""
        ...


class ConsoleResponder:
    def say(self, message: str, spoken: str | None = None) -> None:
        print(f"[artemis-voice] {message}", flush=True)


@dataclass
class TurnResult:
    kind: str  # ignored | task | stopped | idle | busy | cancelled | exit | error
    transcript: str = ""
    goal: str = ""
    task_id: str | None = None
    status: str | None = None
    detail: str = ""


class VoiceAssistant:
    """Drives one Artemis daemon from spoken (or typed) commands.

    While a task runs the assistant keeps listening, so "stop" always works.
    Only one task runs at a time; a new command during a run is refused
    rather than queued, so the phone never does something unexpected later.
    """

    def __init__(
        self,
        client: Any,
        listener: Listener,
        config: VoiceConfig,
        responder: Responder | None = None,
    ) -> None:
        self.client = client
        self.listener = listener
        self.config = config
        self.responder: Responder = responder or ConsoleResponder()
        self._current: str | None = None
        self._watcher: asyncio.Task[None] | None = None

    # -- public API ---------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._current is not None

    async def run(self, max_turns: int | None = None) -> list[TurnResult]:
        results: list[TurnResult] = []
        failures = 0
        while max_turns is None or len(results) < max_turns:
            try:
                text = await self.listener.hear()
            except SpeechRecognitionError as exc:
                failures += 1
                self.responder.say(f"Reconnaissance vocale impossible : {exc}")
                if failures >= 3:
                    break
                continue
            failures = 0
            if text is None:
                await self.wait_idle()
                break
            result = await self.handle_text(text)
            results.append(result)
            if result.kind == "exit":
                break
        return results

    async def wait_idle(self) -> None:
        if self._watcher is not None:
            await self._watcher

    async def handle_text(self, text: str) -> TurnResult:
        cfg = self.config
        text = text.strip()
        if not text:
            return TurnResult("ignored")
        intent = parse_intent(
            text,
            wake_words=cfg.wake_words,
            require_wake_word=cfg.require_wake_word,
            extra_sensitive_words=cfg.extra_sensitive_words,
        )
        if intent.kind is IntentKind.EMPTY:
            return TurnResult("ignored", transcript=text)
        if intent.kind is IntentKind.EXIT:
            self.responder.say(MSG["bye"])
            return TurnResult("exit", transcript=text)
        if intent.kind is IntentKind.STOP:
            return await self._stop(text)

        if self.busy:
            self.responder.say(MSG["busy"])
            return TurnResult("busy", transcript=text, goal=intent.goal)
        if intent.sensitive and cfg.confirm_sensitive:
            if not await self._confirm(intent.goal):
                self.responder.say(MSG["cancelled"])
                return TurnResult("cancelled", transcript=text, goal=intent.goal)
        return await self._start(text, intent)

    # -- internals ----------------------------------------------------------

    async def _confirm(self, goal: str) -> bool:
        self.responder.say(
            f"Action sensible : « {goal} ». Je confirme ? (oui / non)",
            spoken=f"Action sensible : {goal}. Je confirme ? Réponds oui ou non.",
        )
        for _ in range(1 + max(0, self.config.confirm_retries)):
            try:
                answer = await self.listener.hear()
            except SpeechRecognitionError:
                return False
            if answer is None:
                return False
            if not answer.strip():
                continue
            decision = parse_confirmation(answer)
            if decision is not None:
                return decision
            self.responder.say(MSG["unclear"])
        return False

    async def _start(self, text: str, intent: Intent) -> TurnResult:
        cfg = self.config
        profile = cfg.sensitive_profile if intent.sensitive else cfg.profile
        try:
            handle = await self.client.submit(
                intent.goal, profile=profile, device_serial=cfg.device_serial
            )
        except (ArtemisClientError, ValueError) as exc:
            self.responder.say(f"Impossible de lancer la tâche : {exc}")
            return TurnResult("error", transcript=text, goal=intent.goal, detail=str(exc))
        self._current = handle.task_id
        self._watcher = asyncio.create_task(self._watch(handle.task_id))
        self.responder.say(f"Compris : « {intent.goal} ». Exécution ({profile})…")
        return TurnResult(
            "task",
            transcript=text,
            goal=intent.goal,
            task_id=handle.task_id,
            status=handle.status,
        )

    async def _stop(self, text: str) -> TurnResult:
        interrupt = getattr(self.responder, "interrupt", None)
        if callable(interrupt):
            interrupt()  # silence the voice first: "stop" must feel immediate
        task_id = self._current
        if task_id is None:
            self.responder.say(MSG["idle"])
            return TurnResult("idle", transcript=text)
        try:
            stopped = await self.client.stop(task_id)
        except ArtemisClientError as exc:
            self.responder.say(f"Arrêt impossible : {exc}")
            return TurnResult("error", transcript=text, task_id=task_id, detail=str(exc))
        self.responder.say(MSG["stopped"] if stopped else MSG["stop_requested"])
        return TurnResult("stopped", transcript=text, task_id=task_id)

    async def _watch(self, task_id: str) -> None:
        try:
            result = await self.client.wait_for_task(task_id, timeout=self.config.task_timeout)
            if result.succeeded:
                output = f" Résultat : {str(result.output)[:300]}" if result.output else ""
                self.responder.say(f"Terminé.{output}")
            else:
                reason = result.error or result.status
                self.responder.say(f"La tâche n'a pas abouti ({reason}).")
        except TaskTimeoutError:
            self.responder.say("Délai dépassé, j'arrête la tâche.")
            try:
                await self.client.stop(task_id)
            except ArtemisClientError:
                pass
        except ArtemisClientError as exc:
            self.responder.say(f"Suivi de la tâche impossible : {exc}")
        finally:
            if self._current == task_id:
                self._current = None
