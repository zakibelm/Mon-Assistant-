import asyncio

import pytest
from artemis_client.errors import TaskRejectedError

from artemis_voice import AudioListener, TextListener, VoiceAssistant, VoiceConfig
from artemis_voice.audio import FileSource, pcm_to_wav
from artemis_voice.errors import SpeechRecognitionError

from helpers import Collect, FakeClient, ScriptedSTT, tone


def make(lines, client=None, **cfg):
    client = client or FakeClient()
    say = Collect()
    config = VoiceConfig(**cfg)
    return VoiceAssistant(client, TextListener(lines), config, say), client, say


def test_simple_command_runs_on_flash_and_reports_result():
    assistant, client, say = make(["Artemis ouvre les paramètres"], device_serial="emulator-5554")
    results = asyncio.run(assistant.run())
    assert [r.kind for r in results] == ["task"]
    assert client.submitted == [
        {"goal": "ouvre les paramètres", "profile": "flash", "device_serial": "emulator-5554"}
    ]
    assert say.has("Terminé") and say.has("ok")
    assert not assistant.busy


def test_sensitive_command_needs_spoken_yes_and_uses_pro_profile():
    assistant, client, say = make(["envoie un SMS à Paul", "oui"])
    results = asyncio.run(assistant.run())
    assert results[0].kind == "task"
    assert client.submitted[0]["profile"] == "pro"
    assert say.has("Action sensible")


@pytest.mark.parametrize("answer", ["non", "annule", "oui mais plutôt à Marie", "bof", None])
def test_sensitive_command_cancelled_without_clear_yes(answer):
    lines = ["supprime toutes les photos"] + ([answer] if answer else [])
    assistant, client, say = make(lines)
    results = asyncio.run(assistant.run())
    assert results[0].kind == "cancelled"
    assert client.submitted == []


def test_confirmation_retry_then_yes():
    assistant, client, _ = make(["paye la facture", "hmm", "oui"])
    asyncio.run(assistant.run())
    assert len(client.submitted) == 1


def test_confirmation_can_be_disabled_explicitly():
    assistant, client, _ = make(["envoie un SMS"], confirm_sensitive=False)
    asyncio.run(assistant.run())
    assert client.submitted[0]["profile"] == "pro"  # still uses the careful profile


def test_stop_interrupts_running_task_while_still_listening():
    client = FakeClient(hold=True)
    assistant, client, say = make(["ouvre Chrome", "stop"], client=client)
    results = asyncio.run(assistant.run())
    assert [r.kind for r in results] == ["task", "stopped"]
    assert client.stopped == ["task-1"]
    assert say.has("Tâche arrêtée") and say.has("n'a pas abouti")


def test_new_command_refused_while_busy():
    client = FakeClient(hold=True)
    assistant, client, say = make(["ouvre Chrome", "ouvre Gmail", "stop"], client=client)
    results = asyncio.run(assistant.run())
    assert [r.kind for r in results] == ["task", "busy", "stopped"]
    assert len(client.submitted) == 1


def test_stop_with_nothing_running():
    assistant, _, say = make(["stop"])
    assert asyncio.run(assistant.run())[0].kind == "idle"
    assert say.has("Aucune tâche")


def test_exit_ends_session():
    assistant, client, _ = make(["quitter", "ouvre Chrome"])
    results = asyncio.run(assistant.run())
    assert [r.kind for r in results] == ["exit"] and client.submitted == []


def test_wake_word_required_ignores_other_speech():
    assistant, client, _ = make(["ouvre Chrome", "Artemis ouvre Gmail"], require_wake_word=True)
    results = asyncio.run(assistant.run())
    assert [r.kind for r in results] == ["ignored", "task"]
    assert client.submitted[0]["goal"] == "ouvre Gmail"


def test_submit_errors_are_reported_not_raised():
    client = FakeClient()
    client.fail_submit = TaskRejectedError("device busy")
    assistant, _, say = make(["ouvre Chrome"], client=client)
    assert asyncio.run(assistant.run())[0].kind == "error"
    assert say.has("Impossible de lancer")


def test_failed_task_is_reported():
    assistant, _, say = make(["ouvre Chrome"], client=FakeClient(status="failed"))
    asyncio.run(assistant.run())
    assert say.has("n'a pas abouti (failed)")


def test_audio_pipeline_filters_hallucinations(tmp_path):
    wavs = []
    for i in range(3):
        p = tmp_path / f"{i}.wav"
        p.write_bytes(pcm_to_wav(tone(400), 16000))
        wavs.append(str(p))
    stt = ScriptedSTT("Thanks for watching!", "Artémis, ouvre Gmail", "")
    client, say = FakeClient(), Collect()
    assistant = VoiceAssistant(client, AudioListener(FileSource(wavs), stt), VoiceConfig(), say)
    results = asyncio.run(assistant.run())
    assert [r.kind for r in results] == ["ignored", "task", "ignored"]
    assert client.submitted[0]["goal"] == "ouvre Gmail"


def test_repeated_stt_failures_abort_the_loop():
    class Broken:
        async def hear(self):
            raise SpeechRecognitionError("model missing")

    say = Collect()
    assistant = VoiceAssistant(FakeClient(), Broken(), VoiceConfig(), say)
    assert asyncio.run(assistant.run()) == []
    assert sum("impossible" in m for m in say.messages) == 3


def test_microphone_without_portaudio_gives_actionable_error(monkeypatch):
    import builtins
    import artemis_voice.audio.sources as sources
    from artemis_voice.errors import AudioError

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "sounddevice":
            raise OSError("PortAudio library not found")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(AudioError, match="PortAudio"):
        sources.MicrophoneSource(VoiceConfig())
