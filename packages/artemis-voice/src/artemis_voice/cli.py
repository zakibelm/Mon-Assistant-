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

"""``artemis-voice`` command line."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from artemis_voice.config import DEFAULT_WAKE_WORDS, VoiceConfig
from artemis_voice.errors import VoiceError
from artemis_voice.security import validate_endpoint
from artemis_voice.stt import PROVIDERS, create_stt
from artemis_voice.tts import PROVIDERS as TTS_PROVIDERS


def _add_common(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("Artemis daemon")
    g.add_argument("--url", default=os.environ.get("ARTEMIS_BASE_URL", "http://127.0.0.1:8000"))
    g.add_argument(
        "--token", default=os.environ.get("ARTEMIS_TOKEN"), help="Bearer token (HTTPS proxies)"
    )
    g.add_argument("--allow-insecure", action="store_true", help="Allow a plain-HTTP remote daemon")
    g.add_argument("--device", dest="device_serial", help="ADB serial, e.g. emulator-5554")
    g.add_argument("--profile", default="flash", choices=["flash", "pro"])
    g.add_argument("--sensitive-profile", default="pro", choices=["flash", "pro"])
    g.add_argument("--task-timeout", type=float, default=600.0)

    s = parser.add_argument_group("Speech")
    s.add_argument("--stt", default="faster-whisper", choices=PROVIDERS)
    s.add_argument(
        "--model", default=None, help="Whisper model (default: small / whisper-large-v3-turbo)"
    )
    s.add_argument("--language", default=None, help="fr, en, ar ... (default: auto-detect)")
    s.add_argument("--stt-device", default="auto")
    s.add_argument("--compute-type", default="int8")
    s.add_argument(
        "--stt-base-url", default=None, help="OpenAI-compatible base URL (default: Groq)"
    )
    s.add_argument("--stt-api-key", default=None)
    s.add_argument("--stt-api-key-env", default="GROQ_API_KEY")

    t = parser.add_argument_group("Voice replies (TTS)")
    t.add_argument(
        "--tts",
        default="auto",
        choices=TTS_PROVIDERS,
        help="auto = system voice if present (espeak-ng, say, PowerShell); none = text only",
    )
    t.add_argument("--tts-command", default=None, help='e.g. "espeak-ng -v fr-ca" (text on stdin)')
    t.add_argument("--tts-voice", default=None)
    t.add_argument("--tts-language", default=None, help="default: --language, else fr")
    t.add_argument("--piper-model", default=None, help="path to a Piper .onnx voice")
    t.add_argument("--player", default=None, help='WAV player, e.g. "aplay -q {file}"')
    t.add_argument("--tts-base-url", default=None, help="OpenAI-compatible speech API")
    t.add_argument("--tts-model", default=None)
    t.add_argument("--tts-api-key", default=None)
    t.add_argument("--tts-api-key-env", default="OPENAI_API_KEY")

    a = parser.add_argument_group("Assistant")
    a.add_argument("--wake-word", action="append", dest="wake_words", help="repeatable")
    a.add_argument("--require-wake-word", action="store_true")
    a.add_argument("--sensitive-word", action="append", dest="sensitive_words", default=[])
    a.add_argument(
        "--unsafe-skip-confirmation",
        action="store_true",
        help="Do NOT ask for a spoken yes/no before payments, sends, deletions...",
    )


def build_config(args: argparse.Namespace) -> VoiceConfig:
    return VoiceConfig(
        base_url=args.url,
        token=args.token,
        allow_insecure=args.allow_insecure,
        device_serial=args.device_serial,
        profile=args.profile,
        sensitive_profile=args.sensitive_profile,
        task_timeout=args.task_timeout,
        language=args.language,
        wake_words=tuple(args.wake_words) if args.wake_words else DEFAULT_WAKE_WORDS,
        require_wake_word=args.require_wake_word,
        confirm_sensitive=not args.unsafe_skip_confirmation,
        extra_sensitive_words=tuple(args.sensitive_words),
    )


def build_stt(args: argparse.Namespace, config: VoiceConfig, model: str | None = None) -> Any:
    chosen = model or args.model
    if args.stt == "faster-whisper":
        return create_stt(
            "faster-whisper",
            model=chosen or "small",
            device=args.stt_device,
            compute_type=args.compute_type,
            language=config.language,
            vocabulary=config.vocabulary,
        )
    kwargs: dict[str, Any] = {
        "model": chosen or "whisper-large-v3-turbo",
        "api_key": args.stt_api_key,
        "api_key_env": args.stt_api_key_env,
        "language": config.language,
        "vocabulary": config.vocabulary,
    }
    if args.stt_base_url:
        kwargs["base_url"] = args.stt_base_url
    return create_stt("openai", **kwargs)


def _tts_language(args: argparse.Namespace, config: VoiceConfig) -> str:
    return args.tts_language or config.language or "fr"


def _synth_options(args: argparse.Namespace) -> dict[str, Any]:
    if args.tts == "piper":
        if not args.piper_model:
            raise VoiceError("--tts piper needs --piper-model path/to/voice.onnx")
        return {"model": args.piper_model}
    options: dict[str, Any] = {
        "api_key": args.tts_api_key,
        "api_key_env": args.tts_api_key_env,
    }
    if args.tts_base_url:
        options["base_url"] = args.tts_base_url
    if args.tts_model:
        options["model"] = args.tts_model
    if args.tts_voice:
        options["voice"] = args.tts_voice
    return options


def build_tts(args: argparse.Namespace, config: VoiceConfig) -> Any:
    """Speaker for the console assistant, or ``None`` (text only)."""
    from artemis_voice.tts import create_tts

    return create_tts(
        args.tts,
        language=_tts_language(args, config),
        voice=args.tts_voice,
        command=args.tts_command,
        player=args.player,
        synth_options=_synth_options(args) if args.tts in {"piper", "openai"} else None,
    )


def build_synth(args: argparse.Namespace) -> Any:
    """Audio-bytes synthesizer for the web page (``None`` -> browser voice)."""
    if args.tts not in {"piper", "openai"}:
        return None
    from artemis_voice.tts import create_synthesizer

    return create_synthesizer(args.tts, **_synth_options(args))


def build_client(config: VoiceConfig) -> Any:
    validate_endpoint(config.base_url, config.token, allow_insecure=config.allow_insecure)
    from artemis_client import ArtemisClient

    return ArtemisClient(config.base_url, token=config.token, default_profile=config.profile)  # type: ignore[arg-type]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artemis-voice", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("listen", help="Live microphone (needs artemis-voice[mic])")
    _add_common(p)
    p.add_argument("--mic-device", default=None)
    p.add_argument("--energy-threshold", type=float, default=500.0)

    p = sub.add_parser("text", help="Typed commands (no microphone needed)")
    _add_common(p)

    p = sub.add_parser("file", help="Run commands from WAV recordings, one file per command")
    _add_common(p)
    p.add_argument("wav", nargs="+")

    p = sub.add_parser("serve", help="Push-to-talk web page for tablets/phones")
    _add_common(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--allowed-host", action="append", default=[], help="extra Host header value")
    p.add_argument("--web-token", default=os.environ.get("ARTEMIS_VOICE_WEB_TOKEN"))

    p = sub.add_parser("bench", help="Compare Whisper models on your own recordings (WER, speed)")
    _add_common(p)
    p.add_argument("--audio-dir", required=True, help="folder of name.wav + name.txt pairs")
    p.add_argument("--models", default="tiny,base,small", help="comma-separated model names")

    p = sub.add_parser("doctor", help="Check endpoint security, daemon, Whisper and microphone")
    _add_common(p)
    return parser


async def _run_assistant(args: argparse.Namespace, config: VoiceConfig) -> int:
    from artemis_voice.listener import AudioListener, StdinListener
    from artemis_voice.session import VoiceAssistant
    from artemis_voice.speech import SpokenResponder

    client = build_client(config)
    tts = build_tts(args, config)
    responder: SpokenResponder | None = None
    if tts is not None:
        responder = SpokenResponder(tts, language=_tts_language(args, config))
    elif args.tts == "auto":
        print("[artemis-voice] Pas de voix système trouvée : réponses en texte seulement.")
    if args.command == "text":
        listener: Any = StdinListener()
    else:
        from artemis_voice.audio import FileSource, MicrophoneSource

        if args.command == "file":
            source: Any = FileSource(args.wav)
        else:
            config = _with_threshold(config, args.energy_threshold)
            source = MicrophoneSource(
                config,
                device=args.mic_device,
                mute_while=(lambda: responder.is_speaking) if responder else None,
            )
        listener = AudioListener(source, build_stt(args, config))
    assistant = VoiceAssistant(client, listener, config, responder)
    print("[artemis-voice] Prêt. Dis « stop » pour arrêter la tâche, « quitter » pour sortir.")
    try:
        await assistant.run()
    finally:
        if responder is not None:
            responder.wait_idle(5)  # let the goodbye finish
            responder.close()
    return 0


def _with_threshold(config: VoiceConfig, threshold: float) -> VoiceConfig:
    from dataclasses import replace

    return replace(config, energy_threshold=threshold)


def _serve(args: argparse.Namespace, config: VoiceConfig) -> int:
    from artemis_voice.web.server import VoiceWebApp, serve

    app = VoiceWebApp(
        build_client(config),
        build_stt(args, config),
        config,
        token=args.web_token,
        allowed_hosts=tuple(args.allowed_host),
        synth=build_synth(args),
    )
    server = serve(app, host=args.host, port=args.port)
    print(f"[artemis-voice] Page vocale : http://{args.host}:{args.port}/  (Ctrl+C pour arrêter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _bench(args: argparse.Namespace, config: VoiceConfig) -> int:
    from artemis_voice.bench import load_cases, run_benchmark

    cases = load_cases(args.audio_dir)
    if not cases:
        print("Aucun couple name.wav + name.txt trouvé dans", args.audio_dir, file=sys.stderr)
        return 2
    print(f"{len(cases)} enregistrements. WER = taux d'erreur de mots (plus bas = mieux).")
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        result = run_benchmark(build_stt(args, config, model=model), cases)
        print(
            f"{model:<24} WER moyen {result.mean_wer:6.1%}   "
            f"vitesse x{result.realtime_factor:.2f} du temps réel"
        )
        for row in result.rows:
            print(f"    {row.name:<28} WER {row.wer:5.1%}  « {row.hypothesis} »")
    return 0


def _doctor(args: argparse.Namespace, config: VoiceConfig) -> int:
    ok = True

    def line(good: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"[{'OK' if good else '!!'}] {label}{(' — ' + detail) if detail else ''}")

    try:
        validate_endpoint(config.base_url, config.token, allow_insecure=config.allow_insecure)
        line(True, "Point d'accès sécurisé", config.base_url)
        try:
            health = asyncio.run(build_client(config).health())
            line(True, "Démon Artemis joignable", str(dict(health))[:80])
        except Exception as exc:  # network/protocol errors are all reportable
            line(False, "Démon Artemis injoignable", str(exc))
    except VoiceError as exc:
        line(False, "Point d'accès refusé", str(exc))
    for module, label, extra in (
        ("faster_whisper", "faster-whisper", "local"),
        ("sounddevice", "sounddevice (micro)", "mic"),
        ("numpy", "numpy", "local"),
    ):
        try:
            __import__(module)
            line(True, label)
        except ImportError:
            line(False, label + " absent", f"pip install 'artemis-voice[{extra}]'")
        except OSError as exc:  # e.g. sounddevice installed but PortAudio missing
            line(False, label + " inutilisable", str(exc))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = build_config(args)
    try:
        if args.command in {"listen", "text", "file"}:
            return asyncio.run(_run_assistant(args, config))
        if args.command == "serve":
            return _serve(args, config)
        if args.command == "bench":
            return _bench(args, config)
        return _doctor(args, config)
    except VoiceError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
