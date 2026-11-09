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

## Durcissement du démon et des notifications

- **Jeton d'API** : avec `ARTEMIS_API_TOKEN=...`, toute requête (HTTP ou WebSocket, lectures comprises) vers `/api/`, `/images/`, `/videos/` et `/local_file` exige `Authorization: Bearer <jeton>` (schéma exact, jeton nu refusé), y compris le flux d'écran `/api/stream/device-live` et `/api/status`. Seul `/api/health` (`{"status":"ready"}`, sans objectif ni session) reste ouvert. Sans la variable, rien ne change en boucle locale ; la console web du navigateur ne peut plus lire ces routes quand le jeton est défini (utilise le client ou un tunnel Tailscale/SSH).
- **Écoute distante** : refusée sans jeton, que le serveur soit lancé par la commande Artemis ou directement par `uvicorn` (les images Docker définissent `ARTEMIS_BIND_REMOTE=1`, donc le conteneur ne démarre pas sans `ARTEMIS_API_TOKEN`).
- **Confirmation des actions sensibles (page vocale)** : un jeton de 2 minutes, à usage unique (consommation atomique, testée à 16 requêtes simultanées) et lié à la phrase exacte. Un simple `confirmed: true` ne vaut rien. Pour une **preuve humaine indépendante**, définis `ARTEMIS_APPROVAL_PIN` sur le serveur : l'action exige alors ce code en plus du jeton d'API et du jeton de confirmation. Tu le tapes toi-même sur la page ; ne le donne jamais à un script ou à un agent. Après 5 erreurs, verrouillage 60 s.
- **Profil production** : avec `ARTEMIS_ENV=production`, le démon exige un jeton d'API au démarrage sans autre variable, les actions sensibles de la page vocale sont refusées tant que `ARTEMIS_APPROVAL_PIN` n'est pas défini, et `--unsafe-skip-confirmation`, `--allow-insecure`, `ARTEMIS_ALLOW_INSECURE_REMOTE` et `ARTEMIS_NOTIFY_SHELL` sont refusés ou ignorés.
- **`ARTEMIS_NOTIFY_CMD` sans shell par défaut** : la commande est découpée en arguments, le texte des événements reste une simple donnée. Pour des pipes ou redirections, activer `ARTEMIS_NOTIFY_SHELL=1` (mode shell historique, valeurs échappées).
- **CI** : les tests d'`artemis-voice` et des notificateurs tournent à chaque push et pull request.
- Dépendance `posthog` inutilisée retirée.

## Limites connues (sécurité)

- Le jeton est un secret partagé unique, sans rôles par opération ni par appareil.
- La détection d'actions sensibles reste lexicale (listes de mots FR/EN/AR) : une paraphrase peut passer. Un *Policy Engine* côté moteur, qui contrôle l'effet réel avant paiement, envoi ou suppression, reste à construire. Tant qu'il n'existe pas, ne branche pas Mon-Assistant sur un téléphone contenant des données sensibles ni sur un réseau non maîtrisé.
- Contenu hostile affiché à l'écran ou voix externe (injection) : non testé.
- Suite `tests/unit` complète : au moins un test amont échoue encore (`test_turn_index_snapshot`), donc elle n'est pas bloquante en CI.

## Limites connues

- Vrai modèle Whisper, vrai micro, vrai téléphone et voix TTS réelles : non testés dans l'environnement de build (145 tests avec doublures et faux démon).
- Dépendance `posthog` inutilisée côté Artemis et service d'accessibilité sur l'appareil : à vérifier chez toi (voir l'audit).

## Tests

```bash
cd packages/artemis-voice && pip install -e ".[dev]" && pytest -q
```

## Licence

Apache 2.0, comme Artemis.

## Moteur de politique (Policy Engine v1)

Un contrôle d'autorisation central se place juste avant tout effet sur le téléphone, quel que soit le
point d'entrée (voix, API, MCP, CLI). Décisions : `ALLOW`, `REQUIRE_APPROVAL`, `DENY`, `UNKNOWN`
(bloqué). Les approbations passent par un canal séparé (CLI locale ou jeton d'approbateur distinct du
jeton API). **Statut : NO-GO production** — non testé sur téléphone réel. Détails, règles, couverture,
limites et rapport Red Team : [`docs/POLICY-ENGINE.md`](docs/POLICY-ENGINE.md).
