# artemis-voice — commande vocale pour Artemis

Parle, Whisper transcrit, Artemis exécute sur le téléphone Android.

```
micro / tablette ─► Whisper (local ou API) ─► intention ─► [confirmation] ─► Artemis ─► téléphone
```

Le paquet n'embarque ni ADB ni agent : il passe par le SDK distant `artemis-client`
et parle au démon Artemis par HTTP. Il peut donc tourner sur une autre machine que
celle où le téléphone est branché.

## Installation

```bash
pip install -e packages/artemis-client
pip install -e "packages/artemis-voice[local]"        # Whisper local (gratuit, hors ligne)
pip install -e "packages/artemis-voice[local,mic]"    # + micro direct (poste avec micro)
artemis-voice doctor                                  # vérifie sécurité, démon, Whisper, micro
```

## Trois façons de parler à Artemis

| Mode | Commande | Pour qui |
|---|---|---|
| **Page push-to-talk** | `artemis-voice serve` | Tablette / téléphone : le navigateur enregistre, le serveur transcrit |
| **Micro direct** | `artemis-voice listen` | PC avec micro, écoute continue |
| **Texte** | `artemis-voice text` | Sans micro (mêmes règles de sécurité) |
| Fichiers WAV | `artemis-voice file a.wav b.wav` | Enregistrements existants |

### Depuis une tablette (page push-to-talk)

1. Sur la machine qui voit le téléphone (ADB) : démarrer Artemis comme d'habitude (voir le README
   principal), puis `artemis-voice serve --web-token "$(openssl rand -hex 16)"`.
2. Ouvrir la page depuis la tablette **en HTTPS ou via localhost** (le navigateur refuse
   le micro sinon) :
   - tunnel SSH : `ssh -L 8765:127.0.0.1:8765 utilisateur@hote` puis `http://localhost:8765`,
   - ou Tailscale : `tailscale serve --https=443 localhost:8765`, puis ouvrir l'URL `*.ts.net`
     et lancer le serveur avec `--allowed-host <nom>.ts.net`.
3. Maintenir le bouton, parler, relâcher. La phrase comprise s'affiche, modifiable ;
   un appui sur **Exécuter** la lance. Les actions sensibles demandent « Confirmer et exécuter ».

### Micro direct

```bash
artemis-voice listen --language fr --require-wake-word
# « Artemis, ouvre les paramètres Wi-Fi »   → exécute
# « Artemis, envoie un message à Paul »      → « Action sensible… confirmer ? » → « oui »
# « stop » / « arrête »                      → interrompt la tâche en cours (toujours actif)
# « quitter »                                → ferme l'assistant
```

## Réponses vocales (TTS)

Artemis te répond maintenant à voix haute : tâche lancée, terminée ou échouée, demande de confirmation, erreurs. Le texte reste toujours affiché ; sans voix disponible, tout continue en texte seulement.

```bash
artemis-voice listen                                   # voix système automatique (espeak-ng, say, PowerShell)
artemis-voice listen --tts command --tts-command "espeak-ng -v fr-ca"
artemis-voice listen --tts piper --piper-model fr_FR-siwis-medium.onnx   # voix locale neuronale
artemis-voice listen --tts openai --tts-base-url https://api.openai.com/v1 --tts-voice alloy
artemis-voice listen --tts none                        # texte seulement
```

- **Pas de shell** : le texte à dire passe par l'entrée standard (ou un seul argument `{text}`), jamais par une chaîne shell.
- **Le micro se coupe pendant que l'assistant parle**, avec une courte marge après, pour qu'il ne s'entende pas lui-même.
- **« stop » coupe la voix** avant d'arrêter la tâche.
- **Page tablette** : case « 🔊 Réponses vocales » (mémorisée). Voix du serveur si `--tts piper|openai` est donné à `serve` (`/api/speak`, mêmes protections jeton + en-tête anti-CSRF), sinon voix du navigateur (fr-CA, puis fr-FR).
- Avec `--tts openai`, les phrases de réponse sont envoyées au fournisseur choisi. Préfère `command` ou `piper` si elles doivent rester sur ta machine.

## Règles de sécurité (toutes actives par défaut)

- **Point d'accès contrôlé** : le démon Artemis n'a pas d'authentification. La voix refuse tout
  point d'accès autre que loopback, Tailscale ou HTTPS + jeton (`--allow-insecure` pour forcer).
- **Actions sensibles** (payer, envoyer, supprimer, appeler, mot de passe… en FR/EN/AR) :
  confirmation « oui / non » obligatoire, et profil Artemis **Pro** (vérification) au lieu de Flash.
  Un « oui » doit être la phrase entière : « oui mais envoie à Marie » n'est pas un oui.
  `--sensitive-word mot` ajoute vos propres mots ; `--unsafe-skip-confirmation` désactive la
  confirmation (déconseillé).
- **Une tâche à la fois** : une nouvelle commande pendant une exécution est refusée ; « stop » reste
  entendu pendant l'exécution.
- **Mot d'activation** (`--require-wake-word`) : ignore les voix ambiantes (télé, collègues).
- **Anti-hallucinations Whisper** : « Merci d'avoir regardé… », « Sous-titrage… » sur du silence
  sont écartés avant toute exécution.
- **Page web durcie** : liaison loopback, jeton `Authorization: Bearer`, liste de `Host` autorisés
  (anti DNS-rebinding), en-tête `X-Artemis-Voice` + contrôle d'`Origin` (anti-CSRF), et le
  serveur revérifie lui-même la sensibilité : le navigateur ne peut pas sauter la confirmation.

## Choisir le modèle Whisper par test, pas par supposition

Enregistrez 10 à 20 phrases réelles (français québécois, anglais, arabe/darija, noms d'apps) en
`phrase1.wav` + `phrase1.txt` (le texte exact dit), puis :

```bash
artemis-voice bench --audio-dir ./mes_phrases --models tiny,base,small,medium --language fr
```

Le taux d'erreur de mots (WER) et la vitesse (× temps réel) sont mesurés sur votre voix et votre
matériel. `--language` fixé (fr) est plus fiable que l'auto-détection pour des phrases courtes.

## Fournisseurs de transcription interchangeables

| `--stt` | Description | Données |
|---|---|---|
| `faster-whisper` (défaut) | Whisper local, gratuit | L'audio reste sur la machine |
| `openai` | API compatible OpenAI (`--stt-base-url`, par défaut Groq, clé dans `GROQ_API_KEY`) | L'audio est envoyé au fournisseur |

Ajouter un moteur = une classe avec `transcribe(pcm, sample_rate) -> Transcript` et une ligne
dans `artemis_voice/stt/__init__.py`.

## Limites connues

- Voix TTS (système, Piper, API) et sortie audio réelle : testées avec des doublures (45 tests), pas avec de vrais binaires ni haut-parleurs. Disponibilité du français selon la voix installée.

- Les mots sensibles forment une heuristique (FR/EN/AR) : elle réduit le risque, elle ne le supprime
  pas. Gardez le profil Pro et un émulateur dédié pour les parcours à risque.
- Artemis exige le débogage USB (ou un émulateur) et installe un service d'accessibilité sur
  l'appareil : préférez un appareil/émulateur dédié.
- Pas de synthèse vocale en retour : les réponses s'affichent (console ou page).
- Testé ici avec des doublures (faux Whisper, faux démon) : le premier lancement réel télécharge le
  modèle Whisper ; `artemis-voice doctor` puis `bench` confirment que tout fonctionne chez vous.

## Développement

```bash
cd packages/artemis-voice && pip install -e ".[dev]" && pytest
```
