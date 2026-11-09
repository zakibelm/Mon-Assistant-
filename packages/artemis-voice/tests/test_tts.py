import json
import os
import subprocess
import sys
import threading
import time
import urllib.error

import pytest

from artemis_voice import VoiceAssistant, VoiceConfig
from artemis_voice.audio import UtteranceCollector, feed_unless_muted
from artemis_voice.errors import TTSError
from artemis_voice.listener import TextListener
from artemis_voice.speech import SpokenResponder
from artemis_voice.tts import (
    CommandTTS,
    OpenAICompatSynth,
    PiperSynth,
    SynthesizingTTS,
    WavPlayer,
    clean_for_speech,
    create_tts,
    default_command,
)
from artemis_voice.tts.synth import default_player

from helpers import FakeClient, tone

PY = sys.executable


class FakeTTS:
    name = "fake"

    def __init__(self, delay=0.0, fail=False):
        self.spoken, self.delay, self.fail, self.interrupts = [], delay, fail, 0
        self._stop = threading.Event()

    def speak(self, text, *, language=None):
        if self.fail:
            raise TTSError("no voice installed")
        self._stop.clear()
        self.spoken.append((text, language))
        self._stop.wait(self.delay)

    def interrupt(self):
        self.interrupts += 1
        self._stop.set()


def make_responder(tts, **kw):
    printed = []
    kw.setdefault("tail_seconds", 0.0)
    return SpokenResponder(tts, printer=printed.append, **kw), printed


# ---------------------------------------------------------------- text prep
def test_clean_for_speech_removes_noise_and_option_lookalikes():
    assert (
        clean_for_speech("✅ Terminé. Résultat : « 87 % »\n\nOK 🎉")
        == "Terminé. Résultat : 87 % OK"
    )
    assert clean_for_speech("--help") == "help"
    assert clean_for_speech("Exécution (flash)…") == "Exécution (flash)."


def test_clean_for_speech_truncates_on_word_boundary():
    out = clean_for_speech("mot " * 200, max_chars=50)
    assert len(out) <= 51 and out.endswith("mot.")


# ---------------------------------------------------------------- command TTS
def fake_speaker(tmp_path, extra=""):
    out = tmp_path / "heard.txt"
    script = (
        f"import sys; open({str(out)!r}, 'w', encoding='utf-8').write(sys.stdin.read()); {extra}"
    )
    return [PY, "-c", script], out


def test_text_goes_through_stdin_never_a_shell(tmp_path):
    argv, out = fake_speaker(tmp_path)
    marker = tmp_path / "pwned"
    CommandTTS(argv).speak(f"'; touch {marker}; echo $(touch {marker}) `touch {marker}`")
    assert not marker.exists()
    assert "touch" in out.read_text(encoding="utf-8")


def test_text_placeholder_is_one_argument(tmp_path):
    out = tmp_path / "arg.txt"
    script = f"import sys; open({str(out)!r}, 'w').write('|'.join(sys.argv[1:]))"
    CommandTTS([PY, "-c", script, "{lang}", "{text}"]).speak("a b; c", language="fr-CA")
    assert out.read_text() == "fr|a b; c"


def test_command_errors_are_ttserrors(tmp_path):
    with pytest.raises(TTSError):
        CommandTTS(["/nonexistent/binary-xyz"]).speak("x")
    with pytest.raises(TTSError):
        CommandTTS([PY, "-c", "import sys; sys.exit(3)"]).speak("x")
    with pytest.raises(TTSError):
        CommandTTS([])
    with pytest.raises(TTSError):
        CommandTTS([PY, "-c", "import time; time.sleep(30)"], timeout=0.3).speak("x")


def test_interrupt_stops_a_long_utterance():
    tts = CommandTTS([PY, "-c", "import time; time.sleep(30)"])
    errors = []

    def run():
        try:
            tts.speak("long")
        except TTSError as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    started = time.monotonic()
    thread.start()
    time.sleep(0.4)
    tts.interrupt()
    thread.join(5)
    assert not thread.is_alive() and time.monotonic() - started < 5
    assert errors == []  # an interruption is not a failure


def which_only(*names):
    return lambda n: f"/usr/bin/{n}" if n in names else None


def test_default_command_detection():
    assert default_command("fr-CA", which=which_only("say"), platform="darwin") == [
        "say",
        "-v",
        "Amélie",
    ]
    assert default_command(
        "en", which=which_only("espeak-ng"), platform="linux", os_name="posix"
    ) == ["espeak-ng", "-v", "en"]
    assert default_command(
        "fr", which=which_only("spd-say"), platform="linux", os_name="posix"
    ) == ["spd-say", "-e", "-l", "fr"]
    win = default_command("fr", which=which_only("powershell"), platform="win32", os_name="nt")
    assert win and win[0].endswith("powershell") and "SpeechSynthesizer" in win[-1]
    assert default_command("fr", which=which_only(), platform="linux", os_name="posix") is None
    assert (
        default_command(
            "fr", voice="mb-fr1", which=which_only("espeak-ng"), platform="linux", os_name="posix"
        )[-1]
        == "mb-fr1"
    )


def test_create_tts_variants(monkeypatch):
    assert create_tts("none") is None
    monkeypatch.setattr("artemis_voice.tts.default_command", lambda *a, **k: None)
    assert create_tts("auto") is None  # silent, not an error
    with pytest.raises(TTSError):
        create_tts("command")
    custom = create_tts("command", command='espeak-ng -v "fr-be"')
    assert isinstance(custom, CommandTTS) and custom.argv == ["espeak-ng", "-v", "fr-be"]


# ---------------------------------------------------------------- synthesizers
def test_piper_synth_with_fake_binary(tmp_path):
    fake = tmp_path / "piper.py"
    fake.write_text(
        "import sys\nargs=sys.argv\nout=args[args.index('--output_file')+1]\n"
        "open(out,'wb').write(b'RIFF'+sys.stdin.buffer.read())\n"
    )
    runner = lambda argv, **kw: subprocess.run([PY, str(fake), *argv[1:]], **kw)  # noqa: E731
    assert PiperSynth("voice.onnx", runner=runner).synthesize("bonjour") == b"RIFFbonjour"


def test_piper_failures_are_ttserrors():
    bad = lambda argv, **kw: subprocess.CompletedProcess(argv, 2, b"", b"")  # noqa: E731
    with pytest.raises(TTSError):
        PiperSynth("v.onnx", runner=bad).synthesize("x")
    empty = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b"", b"")  # noqa: E731
    with pytest.raises(TTSError):
        PiperSynth("v.onnx", runner=empty).synthesize("x")


def test_openai_synth_request_and_errors(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"RIFFwav"

    def opener(request, timeout):
        seen["req"] = request
        return Resp()

    synth = OpenAICompatSynth(api_key="k", voice="nova", opener=opener)
    assert synth.synthesize("Bonjour") == b"RIFFwav"
    req = seen["req"]
    assert req.full_url.endswith("/audio/speech") and req.get_header("Authorization") == "Bearer k"
    body = json.loads(req.data)
    assert (
        body["input"] == "Bonjour" and body["voice"] == "nova" and body["response_format"] == "wav"
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(TTSError):
        OpenAICompatSynth().synthesize("x")

    def boom(request, timeout):
        raise urllib.error.HTTPError("u", 401, "no", {}, None)

    with pytest.raises(TTSError, match="401"):
        OpenAICompatSynth(api_key="k", opener=boom).synthesize("x")


def test_wav_player_substitutes_file_and_cleans_up(tmp_path):
    out = tmp_path / "copy.wav"
    script = f"import sys,shutil; shutil.copy(sys.argv[1], {str(out)!r})"
    WavPlayer([PY, "-c", script, "{file}"]).play(b"RIFFdata")
    assert out.read_bytes() == b"RIFFdata"
    with pytest.raises(TTSError):
        WavPlayer(["/nonexistent/player", "{file}"]).play(b"x")


def test_default_player_detection():
    assert default_player(which=which_only("aplay"), platform="linux", os_name="posix") == [
        "aplay",
        "-q",
        "{file}",
    ]
    assert default_player(which=which_only(), platform="linux", os_name="posix") is None


def test_synthesizing_tts_chains_synth_and_player():
    played = []

    class Synth:
        name = "s"

        def synthesize(self, text, *, language=None):
            return f"{text}|{language}".encode()

    class Player:
        def play(self, wav):
            played.append(wav)

        def interrupt(self):
            played.append(b"interrupted")

    tts = SynthesizingTTS(Synth(), Player())
    tts.speak("salut", language="fr")
    tts.interrupt()
    assert played == [b"salut|fr", b"interrupted"]


# ---------------------------------------------------------------- SpokenResponder
def test_responder_prints_and_speaks_in_order():
    tts = FakeTTS()
    responder, printed = make_responder(tts, language="fr")
    responder.say("✅ Un")
    responder.say("Deux", spoken="Deux parlé")
    assert responder.wait_idle(5)
    assert printed == ["✅ Un", "Deux"]
    assert tts.spoken == [("Un", "fr"), ("Deux parlé", "fr")]
    responder.close()


def test_is_speaking_covers_queue_playback_and_tail():
    tts = FakeTTS(delay=0.3)
    responder, _ = make_responder(tts, tail_seconds=0.3)
    assert not responder.is_speaking
    responder.say("bonjour")
    assert responder.is_speaking  # immediately, before the worker starts
    time.sleep(0.45)  # playback finished, still inside the tail
    assert responder.is_speaking
    assert responder.wait_idle(5) and not responder.is_speaking
    responder.close()


def test_interrupt_silences_and_drops_queue():
    tts = FakeTTS(delay=0.4)
    responder, _ = make_responder(tts)
    for word in ("un", "deux", "trois"):
        responder.say(word)
    time.sleep(0.1)
    responder.interrupt()
    assert responder.wait_idle(5)
    assert tts.interrupts >= 1 and [t for t, _ in tts.spoken] == ["un"]
    responder.close()


def test_queue_keeps_the_newest_lines():
    tts = FakeTTS(delay=0.3)
    responder, _ = make_responder(tts, max_queue=2)
    for word in ("a", "b", "c", "d", "e"):
        responder.say(word)
    assert responder.wait_idle(10)
    spoken = [t for t, _ in tts.spoken]
    assert spoken[-1] == "e" and len(spoken) <= 3
    responder.close()


def test_broken_voice_disables_itself_but_keeps_printing():
    tts = FakeTTS(fail=True)
    responder, printed = make_responder(tts)
    responder.say("premier")
    assert responder.wait_idle(5)
    responder.say("second")
    assert responder.wait_idle(5)
    assert (
        printed[0] == "premier"
        and any("Voix désactivée" in m for m in printed)
        and printed[-1] == "second"
    )
    responder.close()


def test_empty_after_cleaning_is_not_spoken():
    tts = FakeTTS()
    responder, _ = make_responder(tts)
    responder.say("🎉🎉")
    assert responder.wait_idle(2) and tts.spoken == []
    responder.close()


# ---------------------------------------------------------------- integration with the assistant
def test_stop_silences_the_voice_before_stopping_the_task():
    tts = FakeTTS(delay=0.5)
    responder, _ = make_responder(tts)
    client = FakeClient(hold=True)
    assistant = VoiceAssistant(
        client, TextListener(["ouvre Chrome", "stop"]), VoiceConfig(task_timeout=5), responder
    )
    import asyncio

    asyncio.run(assistant.run())
    assert tts.interrupts >= 1 and client.stopped == ["task-1"]
    responder.close()


def test_confirmation_prompt_has_a_clean_spoken_version():
    import asyncio

    from helpers import Collect

    say = Collect()
    assistant = VoiceAssistant(
        FakeClient(), TextListener(["envoie un SMS à Paul", "oui"]), VoiceConfig(), say
    )
    asyncio.run(assistant.run())
    spoken = next(s for s in say.spoken if s)
    assert "«" not in spoken and "oui ou non" in spoken


def test_microphone_is_muted_while_the_assistant_speaks():
    collector = UtteranceCollector(silence_ms=300)
    speaking = {"on": True}
    muted = lambda: speaking["on"]  # noqa: E731
    frame = tone(30)
    # the assistant's own voice (loud) is ignored entirely
    assert not any(feed_unless_muted(collector, frame, muted) for _ in range(40))
    speaking["on"] = False
    results = [feed_unless_muted(collector, f, muted) for f in [frame] * 20 + [bytes(960)] * 20]
    assert any(results)


# ---------------------------------------------------------------- web /api/speak
def _web_app(synth):
    from artemis_voice.web.server import VoiceWebApp

    return VoiceWebApp(FakeClient(), object(), VoiceConfig(), synth=synth)


class _Synth:
    name = "fake"

    def __init__(self):
        self.calls = []

    def synthesize(self, text, *, language=None):
        self.calls.append(text)
        return b"RIFFwav"


def test_web_speak_cleans_text_and_returns_audio():
    synth = _Synth()
    status, body = _web_app(synth).speak("✅ Terminé « ok »")
    assert status == 200 and body == b"RIFFwav" and synth.calls == ["Terminé ok"]


def test_web_speak_rejects_bad_input_and_missing_provider():
    assert _web_app(None).speak("x")[0] == 501
    app = _web_app(_Synth())
    assert app.speak(None)[0] == 400 and app.speak("   ")[0] == 400
    assert app.speak("a" * 5000)[0] == 413 and app.speak("🎉")[0] == 400


def test_web_speak_provider_failure_is_bad_gateway():
    class Broken:
        name = "b"

        def synthesize(self, text, *, language=None):
            raise TTSError("quota")

    assert _web_app(Broken()).speak("salut") == (502, {"error": "quota"})


def test_web_speak_http_endpoint_enforces_csrf_and_token():
    import http.client

    from artemis_voice.web.server import VoiceWebApp, serve

    app = VoiceWebApp(FakeClient(), object(), VoiceConfig(), token="s3cret", synth=_Synth())
    server = serve(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def post(headers):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/speak", body=json.dumps({"text": "bonjour"}), headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read()

    try:
        ok = {"Authorization": "Bearer s3cret", "X-Artemis-Voice": "1"}
        assert post(ok) == (200, "audio/wav", b"RIFFwav")
        assert post({"X-Artemis-Voice": "1"})[0] == 401
        assert post({"Authorization": "Bearer s3cret"})[0] == 403
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/health", headers={"Authorization": "Bearer s3cret"})
        assert json.loads(conn.getresponse().read())["tts"] is True
    finally:
        server.shutdown()
        server.server_close()
