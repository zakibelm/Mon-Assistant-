import array
import io
import wave

import pytest

from artemis_voice.audio import UtteranceCollector, pcm_to_wav, rms, wav_bytes_to_pcm
from artemis_voice.errors import AudioError, InsecureEndpointError
from artemis_voice.security import validate_endpoint
from artemis_voice.stt.filters import is_hallucination

from helpers import silence, tone

FRAME = 30


def frames(pcm: bytes, rate: int = 16000):
    size = rate * FRAME // 1000 * 2
    return [pcm[i : i + size] for i in range(0, len(pcm) - size + 1, size)]


def feed_all(collector, pcm):
    for f in frames(pcm):
        if collector.feed(f):
            return True
    return False


def test_rms_levels():
    assert rms(silence(30)) == 0
    assert rms(tone(30, 8000)) > 4000


def test_collector_detects_a_spoken_utterance_with_preroll():
    c = UtteranceCollector(silence_ms=600)
    audio = silence(300) + tone(900) + silence(900)
    assert feed_all(c, audio)
    assert len(c.pcm) >= len(tone(900))


def test_collector_ignores_short_clicks_and_keeps_listening():
    c = UtteranceCollector(silence_ms=600, min_utterance_ms=300)
    assert not feed_all(c, silence(300) + tone(90) + silence(900))
    assert feed_all(c, tone(900) + silence(900))


def test_collector_stops_at_max_length():
    c = UtteranceCollector(max_utterance_s=1.0)
    assert feed_all(c, tone(3000))


def test_calibrate_raises_threshold_above_noise():
    c = UtteranceCollector(energy_threshold=100)
    c.calibrate(frames(tone(300, 1000)))
    assert c.threshold > 2000


def test_wav_roundtrip_and_stereo_downmix():
    pcm = tone(100)
    assert wav_bytes_to_pcm(pcm_to_wav(pcm, 16000)) == (pcm, 16000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(array.array("h", [1000, 3000] * 10).tobytes())
    mono, rate = wav_bytes_to_pcm(buf.getvalue())
    assert rate == 8000 and array.array("h", mono).tolist() == [2000] * 10


def test_wav_rejects_garbage_and_8bit():
    with pytest.raises(AudioError):
        wav_bytes_to_pcm(b"not a wav")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(b"\x80" * 10)
    with pytest.raises(AudioError):
        wav_bytes_to_pcm(buf.getvalue())


@pytest.mark.parametrize(
    "url,token",
    [
        ("http://127.0.0.1:8000", None),
        ("http://localhost:8000", None),
        ("http://[::1]:8000", None),
        ("http://100.101.102.103:8000", None),
        ("http://my-host.tail1234.ts.net:8000", None),
        ("https://artemis.example.com", "secret"),
    ],
)
def test_acceptable_endpoints(url, token):
    validate_endpoint(url, token)


@pytest.mark.parametrize(
    "url,token",
    [
        ("http://203.0.113.5:8000", None),
        ("http://203.0.113.5:8000", "tok"),
        ("https://artemis.example.com", None),
        ("ftp://x", None),
    ],
)
def test_unsafe_endpoints_rejected(url, token):
    with pytest.raises(InsecureEndpointError):
        validate_endpoint(url, token)


def test_allow_insecure_override():
    validate_endpoint("http://203.0.113.5:8000", None, allow_insecure=True)


@pytest.mark.parametrize(
    "text",
    [
        "Thanks for watching!",
        "Sous-titrage ST' 501",
        "Sous-titres réalisés par la communauté d'Amara.org",
        "ha ha ha ha ha",
        "",
        ".",
    ],
)
def test_hallucinations_filtered(text):
    assert is_hallucination(text)


def test_real_commands_not_filtered():
    assert not is_hallucination("ouvre les paramètres")
    assert not is_hallucination("open Chrome and search for weather")
