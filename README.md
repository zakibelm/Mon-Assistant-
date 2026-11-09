# Mon-Assistant

Assistant vocal qui pilote un téléphone Android : tu parles, il comprend (Whisper), exécute la tâche avec [Artemis](https://github.com/google/artemis), puis te répond à voix haute.

```
micro / tablette → Whisper → intention (mot d'éveil, stop, actions sensibles) → Artemis → réponse vocale (TTS)
```

## Ce qu'il y a dans ce dépôt

| Dossier | Rôle |
|---|---|
| `packages/artemis-voice/` | Commande vocale : transcription, sécurité, réponses vocales, page tablette |
| `packages/artemis-client/` | SDK sans dépendance pour appeler le démon Artemis |
| `mcp_server/` | Artemis (fork de google/artemis), avec correctif d'injection shell dans `ScriptNotifier` |
| `docs/AUDIT-voice-edition.md` | Audit : problème → action → bénéfice |
| `docs/ARTEMIS-UPSTREAM-README.md` | README d'origine d'Artemis |

## Installation

```bash
pip install -e packages/artemis-client -e "packages/artemis-voice[local,mic]"
artemis-voice doctor        # vérifie démon, Whisper, micro
```

Prérequis : un démon Artemis démarré (par défaut `http://127.0.0.1:8000`) et un téléphone Android relié par ADB.

## Utilisation

```bash
artemis-voice listen                 # micro direct, voix système automatique
artemis-voice text                   # clavier, pour tester sans micro
artemis-voice serve                  # page push-to-talk pour tablette/téléphone
artemis-voice bench --audio-dir ./enregistrements   # compare les modèles Whisper
```

- « Artemis, ouvre les paramètres Wi-Fi » → exécute.
- Action sensible (paiement, envoi, suppression) → demande un « oui » explicite, puis profil Pro.
- « stop » / « arrête » → coupe la voix et la tâche (toujours actif). « quitter » → ferme.

## Transcription (Whisper), interchangeable

`--stt faster-whisper` (local, par défaut) ou `--stt openai` avec `--stt-base-url` (Groq, OpenAI…). Choisis le modèle avec `bench` sur tes propres enregistrements plutôt qu'à l'intuition.

## Réponses vocales (TTS)

```bash
artemis-voice listen --tts command --tts-command "espeak-ng -v fr-ca"
artemis-voice listen --tts piper --piper-model fr_FR-siwis-medium.onnx
artemis-voice listen --tts openai --tts-base-url https://api.openai.com/v1 --tts-voice alloy
artemis-voice listen --tts none      # texte seulement
```

Le texte passe par l'entrée standard, jamais par un shell. Le micro se coupe pendant que l'assistant parle. Avec `--tts openai`, les phrases de réponse sont envoyées au fournisseur.

## Sécurité (active par défaut)

- Point d'accès validé, HTTPS exigé hors boucle locale (sauf `--allow-insecure`).
- Page web : bind local, jeton, liste des Host (anti DNS rebinding), en-tête anti-CSRF, reconfirmation côté serveur.
- Filtre d'hallucinations Whisper, une seule tâche à la fois, « stop » toujours honoré.

## Limites connues

- Vrai modèle Whisper, vrai micro, vrai téléphone et voix TTS réelles : non testés dans l'environnement de build (145 tests avec doublures et faux démon).
- Dépendance `posthog` inutilisée côté Artemis et service d'accessibilité sur l'appareil : à vérifier chez toi (voir l'audit).

## Tests

```bash
cd packages/artemis-voice && pip install -e ".[dev]" && pytest -q
```

## Licence

Apache 2.0, comme Artemis.
