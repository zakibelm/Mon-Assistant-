# Audit Artemis — édition commande vocale

Périmètre : surfaces touchées par une commande vocale (SDK distant, API du démon, liaison réseau,
notifieur de scripts, dépendances). Les ~94 000 lignes du dépôt n'ont pas été relues en entier.
Chaque point suit le format **Problème → Action → Bénéfice**.

## Corrigé dans cette version

**1. Injection de commande dans `ARTEMIS_NOTIFY_CMD`** (`mcp_server/notifiers/script.py`)
- Problème : titre et message de l'événement étaient insérés tels quels dans une commande exécutée
  avec `shell=True`. Un message contenant `'; commande; '` s'exécutait (reproduit sur l'ancien code).
  Le texte vient de tâches qui lisent l'écran d'un téléphone : il n'est pas de confiance.
- Action : `render_command()` échappe chaque valeur selon son contexte (apostrophes, guillemets, nu).
  Les modèles existants comme `--title '{title}'` continuent de fonctionner.
- Bénéfice : plus d'exécution de code via le contenu d'un message. 22 tests couvrent 7 charges
  d'attaque × 3 styles de guillemets, plus un test bout en bout.
- Réserve : sous Windows, `cmd.exe` n'offre pas d'échappement fiable ; les guillemets sont
  neutralisés au mieux. Le plus sûr reste un script qui lit les valeurs en argument.

## Traité par la nouvelle couche voix (`packages/artemis-voice`)

**2. API du démon sans authentification**
- Problème : le README du SDK le confirme ; quiconque atteint le port pilote le téléphone. La liaison
  par défaut est bien loopback (`127.0.0.1`), mais une commande vocale distante risque d'être
  envoyée en clair à travers Internet.
- Action : la voix refuse tout point d'accès hors loopback / Tailscale / HTTPS+jeton ; la page web
  exige un jeton hors loopback, filtre les `Host`, bloque le CSRF et revérifie la sensibilité côté
  serveur.
- Bénéfice : un site web malveillant ou une machine du réseau ne peut pas faire exécuter d'ordres.

**3. Voix ambiantes et erreurs de transcription**
- Problème : Whisper invente du texte sur le silence et une voix de télé peut ressembler à un ordre.
- Action : filtre d'hallucinations, mot d'activation optionnel, phrase affichée et modifiable avant
  exécution sur la page web, « stop » toujours actif.
- Bénéfice : une phrase mal comprise n'atteint pas le téléphone sans que vous l'ayez vue.

**4. Actions irréversibles par la voix** (paiement, envoi, suppression, appel)
- Problème : le profil Flash n'a ni plan de tâche ni filet de sécurité avant exécution.
- Action : mots sensibles FR/EN/AR → confirmation « oui / non » stricte + profil Pro.
- Bénéfice : un ordre risqué demande deux validations et une exécution vérifiée.

## À faire de votre côté (non vérifiable ici)

**5. Dépendance `posthog` déclarée dans `pyproject.toml`**
- Problème : aucun import trouvé dans le code Python, mais le paquet reste installé.
- Action : confirmer qu'aucun module ne l'utilise (dossier `artemis/telemetry` non relu), puis le
  retirer ; sinon désactiver la télémétrie explicitement.
- Bénéfice : surface d'attaque réduite et aucune donnée envoyée à un tiers par inadvertance.

**6. Service d'accessibilité installé sur l'appareil**
- Problème : le README indique qu'un service d'accessibilité est installé au premier usage ; il voit
  tout l'écran.
- Action : utiliser un émulateur ou un téléphone dédié, sans comptes bancaires ni messageries
  personnelles, pour la commande vocale.
- Bénéfice : une erreur de transcription ou d'agent reste confinée.

## Non vérifié dans cet environnement

- Exécution avec un vrai modèle Whisper (téléchargement bloqué ici) et un vrai micro.
- Exécution contre un démon Artemis et un appareil Android réels.
- Action : `artemis-voice doctor`, puis `artemis-voice bench` sur vos propres enregistrements.
- Bénéfice : choix du modèle sur mesures réelles (français québécois, anglais, arabe) plutôt que sur
  la documentation.
