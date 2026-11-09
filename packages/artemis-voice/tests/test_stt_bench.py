import json
from types import SimpleNamespace

import pytest

from artemis_voice.bench import BenchResult, load_cases, run_benchmark, wer
from artemis_voice.audio import pcm_to_wav
from artemis_voice.errors import SpeechRecognitionError
from artemis_voice.stt import FasterWhisperSTT, OpenAICompatSTT, create_stt
from artemis_voice.stt.openai_compat import build_multipart

from helpers import ScriptedSTT, tone


class FakeModel:
    def __init__(self, segments):
        self.segments, self.calls = segments, []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter(self.segments), SimpleNamespace(language="fr")


def seg(text, no_speech=0.0, logprob=-0.2):
    return SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob)


def test_faster_whisper_joins_segments_and_passes_vocabulary():
    pytest.importorskip("numpy")
    model = FakeModel([seg(" Ouvre "), seg("les paramètres")])
    stt = FasterWhisperSTT(
        vocabulary=("Wi-Fi", "Artemis"), language="fr", model_factory=lambda: model
    )
    out = stt.transcribe(tone(500), 16000)
    assert out.text == "Ouvre les paramètres" and out.language == "fr"
    audio, kwargs = model.calls[0]
    assert kwargs["initial_prompt"] == "Wi-Fi, Artemis" and kwargs["language"] == "fr"
    assert kwargs["vad_filter"] is True and kwargs["condition_on_previous_text"] is False
    assert audio.dtype.name == "float32" and abs(audio).max() <= 1.0


def test_faster_whisper_drops_confident_silence_and_resamples():
    pytest.importorskip("numpy")
    model = FakeModel(
        [seg("Merci d'avoir regardé", no_speech=0.9, logprob=-2.0), seg("ouvre Gmail")]
    )
    stt = FasterWhisperSTT(model_factory=lambda: model)
    out = stt.transcribe(tone(500, rate=8000), 8000)
    assert out.text == "ouvre Gmail"
    assert len(model.calls[0][0]) == 16000 * 500 // 1000


def test_openai_compat_request_and_response():
    captured = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"text": " ouvre Chrome ", "language": "fr", "segments": [{"no_speech_prob": 0.1}]}
            ).encode()

    def opener(request, timeout):
        captured["req"], captured["timeout"] = request, timeout
        return Resp()

    stt = OpenAICompatSTT(api_key="k", language="fr", vocabulary=("Artemis",), opener=opener)
    out = stt.transcribe(tone(300), 16000)
    assert out.text == "ouvre Chrome" and out.no_speech_prob == 0.1
    req = captured["req"]
    assert req.full_url.endswith("/audio/transcriptions")
    assert req.get_header("Authorization") == "Bearer k"
    assert b'name="language"' in req.data and b"Artemis" in req.data and b"RIFF" in req.data


def test_openai_compat_requires_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(SpeechRecognitionError):
        OpenAICompatSTT().transcribe(tone(100), 16000)


def test_multipart_shape():
    body, ctype = build_multipart({"a": "1"}, "x.wav", b"DATA")
    boundary = ctype.split("boundary=")[1].encode()
    assert body.startswith(b"--" + boundary) and body.rstrip().endswith(b"--" + boundary + b"--")


def test_create_stt_unknown():
    with pytest.raises(SpeechRecognitionError):
        create_stt("nope")


def test_wer():
    assert wer("ouvre les paramètres", "Ouvre les parametres") == 0
    assert wer("ouvre les paramètres", "ouvre paramètres") == pytest.approx(1 / 3)
    assert wer("", "x") == 1.0


def test_benchmark_roundtrip(tmp_path):
    (tmp_path / "a.wav").write_bytes(pcm_to_wav(tone(500), 16000))
    (tmp_path / "a.txt").write_text("ouvre chrome", encoding="utf-8")
    (tmp_path / "orphan.wav").write_bytes(pcm_to_wav(tone(100), 16000))
    cases = load_cases(str(tmp_path))
    assert len(cases) == 1
    result: BenchResult = run_benchmark(ScriptedSTT("ouvre gmail"), cases)
    assert result.mean_wer == pytest.approx(0.5) and result.realtime_factor >= 0
