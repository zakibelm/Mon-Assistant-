import http.client
import json
import threading

import pytest

from artemis_voice import VoiceConfig
from artemis_voice.audio import pcm_to_wav
from artemis_voice.cli import _build_parser, build_config, main
from artemis_voice.errors import InsecureEndpointError
from artemis_voice.web.server import VoiceWebApp, serve

from helpers import FakeClient, ScriptedSTT, tone

H = {"X-Artemis-Voice": "1"}


@pytest.fixture
def running():
    servers = []

    def start(stt_texts=("ouvre Chrome",), token=None, client=None, allowed=(), **app_kwargs):
        client = client or FakeClient()
        app = VoiceWebApp(
            client,
            ScriptedSTT(*stt_texts),
            VoiceConfig(),
            token=token,
            allowed_hosts=allowed,
            **app_kwargs,
        )
        server = serve(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1], client

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def call(port, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = dict(headers or {})
    if host:
        hdrs["Host"] = host
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except ValueError:
        return resp.status, raw


def jpost(port, path, payload, headers=None):
    h = {"Content-Type": "application/json", **H, **(headers or {})}
    return call(port, "POST", path, json.dumps(payload), h)


def test_page_served_and_has_no_inline_secrets(running):
    port, _ = running()
    status, body = call(port, "GET", "/")
    assert status == 200 and b"Maintenir pour parler" in body


def test_transcribe_then_execute_flow(running):
    port, client = running(("Artemis ouvre Chrome",))
    status, data = call(port, "POST", "/api/transcribe", pcm_to_wav(tone(400), 16000), H)
    assert status == 200 and data["goal"] == "ouvre Chrome" and not data["needs_confirmation"]
    status, data = jpost(port, "/api/execute", {"goal": data["goal"]})
    assert status == 200 and data["profile"] == "flash"
    status, st = call(port, "GET", f"/api/task?id={data['task_id']}")
    assert status == 200 and st["done"] and st["succeeded"]


def test_sensitive_goal_needs_a_server_issued_single_use_token(running):
    port, client = running()
    goal = {"goal": "envoie un SMS à Paul"}
    status, data = jpost(port, "/api/execute", goal)
    assert status == 409 and data["error"] == "confirmation_required" and client.submitted == []
    token = data["confirm_token"]
    status, data = jpost(port, "/api/execute", {**goal, "confirm_token": token})
    assert status == 200 and data["profile"] == "pro"
    # replay is refused (and a fresh token is issued)
    status, again = jpost(port, "/api/execute", {**goal, "confirm_token": token})
    assert status == 409 and again["confirm_token"] != token


def test_bare_confirmed_flag_is_worthless(running):
    port, client = running()
    for flag in (True, "yes", 1):
        status, _ = jpost(port, "/api/execute", {"goal": "supprime les photos", "confirmed": flag})
        assert status == 409
    assert client.submitted == []


def test_token_is_bound_to_the_exact_goal_and_expires(running, monkeypatch):
    port, client = running()
    _, data = jpost(port, "/api/execute", {"goal": "envoie un SMS à Paul"})
    token = data["confirm_token"]
    status, _ = jpost(port, "/api/execute", {"goal": "supprime les photos", "confirm_token": token})
    assert status == 409 and client.submitted == []
    import time as _time

    real = _time.time
    monkeypatch.setattr("artemis_voice.web.server.time.time", lambda: real() + 600)
    status, _ = jpost(
        port, "/api/execute", {"goal": "envoie un SMS à Paul", "confirm_token": token}
    )
    assert status == 409 and client.submitted == []


def test_bare_bearer_token_is_refused():
    from artemis_voice.web.server import VoiceWebApp

    app = VoiceWebApp(object(), object(), VoiceConfig(), token="s3cret")
    assert app.token_ok("Bearer s3cret")
    assert not app.token_ok("s3cret")
    assert not app.token_ok("Basic s3cret")
    assert not app.token_ok(None)


def test_busy_task_is_refused(running):
    port, client = running(client=FakeClient(hold=True))
    assert jpost(port, "/api/execute", {"goal": "ouvre Chrome"})[0] == 200
    status, data = jpost(port, "/api/execute", {"goal": "ouvre Gmail"})
    assert status == 409 and data["error"] == "busy"
    assert jpost(port, "/api/stop", {})[1]["stopped"] is True


def test_csrf_header_required_on_writes(running):
    port, _ = running()
    status, data = call(
        port,
        "POST",
        "/api/execute",
        json.dumps({"goal": "ouvre Chrome"}),
        {"Content-Type": "application/json"},
    )
    assert status == 403 and data["error"] == "missing_csrf_header"


def test_cross_origin_post_rejected(running):
    port, _ = running()
    status, data = jpost(
        port, "/api/execute", {"goal": "ouvre Chrome"}, {"Origin": "https://evil.example"}
    )
    assert status == 403 and data["error"] == "cross_origin"


def test_dns_rebinding_host_rejected(running):
    port, _ = running()
    status, data = call(port, "GET", "/api/health", host="evil.example")
    assert status == 403 and data["error"] == "host_not_allowed"
    assert call(port, "GET", "/api/health", host=f"localhost:{port}")[0] == 200


def test_bearer_token(running):
    port, _ = running(token="s3cret")
    assert call(port, "GET", "/api/health")[0] == 401
    assert call(port, "GET", "/api/health", headers={"Authorization": "Bearer wrong"})[0] == 401
    assert call(port, "GET", "/api/health", headers={"Authorization": "Bearer s3cret"})[0] == 200


def test_bad_inputs(running):
    port, _ = running()
    assert call(port, "POST", "/api/transcribe", b"not wav", H)[0] == 400
    assert jpost(port, "/api/execute", {"goal": ""})[0] == 400
    assert (
        call(port, "POST", "/api/execute", b"{bad", {**H, "Content-Type": "application/json"})[0]
        == 400
    )
    assert jpost(port, "/api/execute", {"goal": "stop"})[0] == 400  # a stop word is not a task
    assert call(port, "GET", "/api/nope")[0] == 404


def test_hallucinated_transcript_returns_empty(running):
    port, _ = running(("Thanks for watching!",))
    status, data = call(port, "POST", "/api/transcribe", pcm_to_wav(tone(400), 16000), H)
    assert status == 200 and data["text"] == "" and data["kind"] == "empty"


def test_non_loopback_bind_requires_token():
    app = VoiceWebApp(FakeClient(), ScriptedSTT(), VoiceConfig())
    with pytest.raises(InsecureEndpointError):
        serve(app, host="0.0.0.0", port=0)


def test_cli_config_and_guardrails(capsys):
    args = _build_parser().parse_args(
        ["text", "--device", "emulator-5554", "--language", "fr", "--sensitive-word", "lance"]
    )
    cfg = build_config(args)
    assert cfg.device_serial == "emulator-5554" and cfg.language == "fr" and cfg.confirm_sensitive
    assert cfg.extra_sensitive_words == ("lance",)
    assert main(["doctor", "--url", "http://203.0.113.9:8000"]) == 1
    assert "refusé" in capsys.readouterr().out
    assert main(["text", "--url", "http://203.0.113.9:8000"]) == 2
    skip = build_config(_build_parser().parse_args(["text", "--unsafe-skip-confirmation"]))
    assert skip.confirm_sensitive is False


def test_unsafe_flags_are_refused_in_production(monkeypatch, capsys):
    monkeypatch.setenv("ARTEMIS_ENV", "production")
    assert main(["text", "--unsafe-skip-confirmation"]) == 2
    assert main(["text", "--url", "http://10.0.0.5:8000", "--allow-insecure"]) == 2
    assert "production" in capsys.readouterr().err


SMS = {"goal": "envoie un SMS à Paul"}


def _token(port):
    return jpost(port, "/api/execute", SMS)[1]["confirm_token"]


def test_pin_is_required_and_checked_before_the_token_is_spent(running):
    port, client = running(approval_pin="4321")
    status, data = jpost(port, "/api/execute", SMS)
    assert status == 409 and data["needs_pin"] is True
    token = data["confirm_token"]
    status, data = jpost(port, "/api/execute", {**SMS, "confirm_token": token})
    assert status == 403 and data["error"] == "invalid_pin"
    status, _ = jpost(port, "/api/execute", {**SMS, "confirm_token": token, "approval_pin": "0000"})
    assert status == 403 and client.submitted == []
    # a wrong PIN did not burn the token
    status, data = jpost(
        port, "/api/execute", {**SMS, "confirm_token": token, "approval_pin": "4321"}
    )
    assert status == 200 and data["profile"] == "pro"


def test_pin_brute_force_is_locked_out(running):
    port, client = running(approval_pin="4321")
    token = _token(port)
    for _ in range(5):
        assert (
            jpost(port, "/api/execute", {**SMS, "confirm_token": token, "approval_pin": "x"})[0]
            == 403
        )
    status, data = jpost(
        port, "/api/execute", {**SMS, "confirm_token": token, "approval_pin": "4321"}
    )
    assert status == 429 and data["error"] == "too_many_attempts" and client.submitted == []


def test_production_refuses_sensitive_actions_without_a_configured_pin(running):
    port, client = running(production=True)
    status, data = jpost(port, "/api/execute", SMS)
    assert status == 403 and data["error"] == "approval_not_configured" and client.submitted == []
    assert (
        jpost(port, "/api/execute", {"goal": "ouvre Chrome"})[0] == 200
    )  # normal tasks unaffected


def test_a_token_can_only_be_spent_once_under_concurrency():
    app = VoiceWebApp(FakeClient(), ScriptedSTT(), VoiceConfig())
    token = app.issue_confirmation("envoie un SMS à Paul")
    wins = []
    barrier = threading.Barrier(16)

    def attempt():
        barrier.wait()
        wins.append(app.consume_confirmation(token, "envoie un SMS à Paul"))

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wins.count(True) == 1
