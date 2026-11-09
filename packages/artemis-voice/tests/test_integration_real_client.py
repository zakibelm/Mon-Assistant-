"""Voice layer driving the *real* artemis-client SDK against a fake HTTP daemon.

This catches protocol drift that mocks cannot: endpoints, payload shapes, the
idempotent session_id, and the stop call.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from artemis_client import ArtemisClient

from artemis_voice import TextListener, VoiceAssistant, VoiceConfig

from helpers import Collect


class FakeDaemon:
    def __init__(self, polls_before_done=1):
        self.runs, self.stops, self.polls = [], [], {}
        self.polls_before_done = polls_before_done
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/api/sessions/"):
                    sid = self.path.rsplit("/", 1)[1]
                    daemon.polls[sid] = daemon.polls.get(sid, 0) + 1
                    if sid in daemon.stops:
                        return self._json({"session_id": sid, "status": "cancelled"})
                    done = daemon.polls[sid] > daemon.polls_before_done
                    return self._json(
                        {
                            "session_id": sid,
                            "status": "completed" if done else "running",
                            "output": "batterie 87 %",
                        }
                    )
                if self.path == "/api/status":
                    return self._json({"queue": [], "active_tasks": []})
                self._json({"error": "nf"}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/run":
                    daemon.runs.append(body)
                    return self._json(
                        {
                            "status": "queued",
                            "tasks": [{"session_id": body["session_id"], "status": "queued"}],
                        }
                    )
                if self.path == "/api/stop":
                    daemon.stops.append(body["session_id"])
                    return self._json({"status": "stopped"})
                self._json({"error": "nf"}, 404)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def daemon():
    d = FakeDaemon()
    yield d
    d.close()


def test_spoken_command_reaches_daemon_with_expected_payload(daemon):
    client = ArtemisClient(daemon.url, poll_interval=0.05)
    say = Collect()
    config = VoiceConfig(base_url=daemon.url, device_serial="emulator-5554", task_timeout=10)
    assistant = VoiceAssistant(
        client, TextListener(["Artemis, vérifie le pourcentage de batterie"]), config, say
    )
    asyncio.run(assistant.run())
    run = daemon.runs[0]
    assert run["goal"] == "vérifie le pourcentage de batterie"
    assert run["profile"] == "flash" and run["device_serial"] == "emulator-5554"
    assert run["ingress"] == "python_sdk" and run["session_id"]
    assert say.has("Terminé") and say.has("batterie 87")


def test_sensitive_command_uses_pro_profile_through_real_client(daemon):
    client = ArtemisClient(daemon.url, poll_interval=0.05)
    assistant = VoiceAssistant(
        client,
        TextListener(["envoie un SMS à Paul", "oui"]),
        VoiceConfig(task_timeout=10),
        Collect(),
    )
    asyncio.run(assistant.run())
    assert daemon.runs[0]["profile"] == "pro"


def test_declined_confirmation_never_reaches_daemon(daemon):
    client = ArtemisClient(daemon.url, poll_interval=0.05)
    assistant = VoiceAssistant(
        client, TextListener(["supprime les photos", "non"]), VoiceConfig(), Collect()
    )
    asyncio.run(assistant.run())
    assert daemon.runs == []


def test_stop_calls_daemon_stop_endpoint():
    d = FakeDaemon(polls_before_done=10_000)
    try:
        client = ArtemisClient(d.url, poll_interval=0.05)
        say = Collect()

        class Slow(TextListener):
            async def hear(self):
                text = await super().hear()
                if text == "stop":
                    await asyncio.sleep(0.3)  # let the task start polling first
                return text

        assistant = VoiceAssistant(
            client, Slow(["ouvre Chrome", "stop"]), VoiceConfig(task_timeout=10), say
        )
        asyncio.run(assistant.run())
        assert d.stops == [d.runs[0]["session_id"]]
        assert say.has("Tâche arrêtée") and say.has("n'a pas abouti (cancelled)")
    finally:
        d.close()
