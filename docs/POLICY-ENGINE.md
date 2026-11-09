# Policy Engine v1.1 — contrôle d'autorisation central

> **Statut : NO-GO pour la production.** Le moteur est implémenté, testé (185 tests dédiés) et
> branché sur toutes les voies d'effet cartographiées, mais **rien n'a été testé sur un vrai
> téléphone Android** et plusieurs limites (section 6) empêchent de le déclarer prêt.
> Branche : `policy-engine-v1` (non fusionnée dans `main`).

## 1. Principe

Une seule règle : **aucun effet n'atteint le téléphone sans passer par `artemis.policy`**, quel que
soit le point d'entrée (page vocale, `/api/run`, MCP, CLI, console). Le contrôle est placé dans le
moteur d'exécution, immédiatement avant l'effet, pas dans les points d'entrée.

```
entrée (voix / API / MCP / CLI) → agent → outil → PolicyGuardedDriver → [Policy Engine] → AndroidAdbDriver → téléphone
                                          └→ command_tool (adb direct) → _policy_gate → [Policy Engine]
```

## 1 bis. Correctifs v1.1 (contre-audit du 9 novembre 2026)

| Constat | Correctif | Test |
|---|---|---|
| P0-01 clic non identifiable autorisé | `Action.ui_known` (défaut `False`). Écran illisible, application inconnue ou contrôle sans libellé dans une app de messagerie/achat → `REQUIRE_APPROVAL`. Les swipes ne sont pas concernés. | `test_v11.py` |
| P0-02 empreinte incomplète | le digest couvre aussi demandeur, application au premier plan, libellé de la cible, `ui_known` et libellés shell. Après une approbation, l'écran est relu : s'il a changé, l'action est refusée (l'approbation est déjà consommée). | `test_v11.py` |
| P1-03 délégation `__getattr__` | liste blanche explicite (`action_history`, `get_ui_hierarchy`), tout le reste lève `AttributeError` ; le pilote interne est en attribut à nom mutilé ; test statique qu'aucun code externe n'y touche. | `test_v11.py` |
| P1-04 audit indisponible | en production (ou `ARTEMIS_POLICY_AUDIT_REQUIRED=1`), même une action `ALLOW` est refusée si la trace ne peut être écrite. | `test_v11.py` |
| Propagation identité/origine | l'origine vient de `ARTEMIS_TASK_INGRESS` (worker, CLI), `ensure_context("mcp")` à l'entrée MCP, session via `ARTEMIS_SESSION_ID`, demandeur via `ARTEMIS_POLICY_REQUESTER` posé par la file de tâches. | `test_v11.py` |

Limite assumée : le demandeur est une **classe** d'appelant (`api-bearer` / `api-anonymous`), pas une
identité par utilisateur, car l'API n'a qu'un jeton partagé. L'origine déclarée par le client
(`ingress`) n'est pas authentifiée : elle sert à l'audit et à la liaison d'approbation, jamais à
assouplir une décision.

## 2. Modèle d'action et décisions

`Action(op, device_id, params, foreground_package, target_label, context, labels)` avec un
`digest()` sha256 (op, paramètres canoniques, appareil, session, origine).

| Décision | Effet |
|---|---|
| `ALLOW` | exécution, journalisée |
| `REQUIRE_APPROVAL` | bloque jusqu'à une approbation hors-bande, usage unique |
| `DENY` | refus définitif |
| `UNKNOWN` | **refus** (échec fermé) — règle absente, commande non analysable, erreur interne |

Les règles sont pures et déterministes (`artemis/policy/rules.py`) : elles ne lisent ni le prompt,
ni le texte de l'écran (hors libellé du bouton touché), ni le but de l'agent. Une injection de
prompt n'a donc aucun canal pour changer une décision.

### Table de règles v1

| Catégorie | Exemples | Décision |
|---|---|---|
| Lecture / navigation | `getprop`, `dumpsys`, `input keyevent 4`, `am start … VIEW https://…`, ouvrir les Paramètres | `ALLOW` |
| Communication | toucher « Envoyer/Send/إرسال », `am start … SENDTO/CALL`, touche Entrée dans une app de messagerie | `REQUIRE_APPROVAL` |
| Modification / destruction | `settings put`, `svc`, `pm uninstall/clear`, `rm` hors zone temporaire, toucher « Supprimer/Delete » | `REQUIRE_APPROVAL` |
| Destruction de zones protégées | `rm -rf /`, `/sdcard`, `/data`… | `DENY` |
| Finance | apps bancaires/paiement, libellés « Payer/Pay/Acheter/Confirm payment » | `DENY` (`ARTEMIS_POLICY_FINANCE=approve` → `REQUIRE_APPROVAL`, jamais `ALLOW`) |
| Binaires interdits | `su`, `reboot`, `dd`, `curl`, `sqlite3`, `run-as`… | `DENY` |
| Enveloppes opaques | `sh -c`, `eval`, `env`, `xargs`, `timeout`, `python`… | `UNKNOWN` |
| Non analysable | `$(…)`, backticks, `$VAR`, redirection hors `/dev/null` | `UNKNOWN` |
| Commande inconnue | tout binaire non classé | `UNKNOWN` |

Le shell est **analysé sans être exécuté** : découpage en segments (`&& || ; | &`, retour ligne)
en respectant les guillemets ; la pire décision de la ligne l'emporte. Un `tap` est jugé d'après
l'élément cliquable le plus petit sous le doigt (lecture de la hiérarchie d'écran).

## 3. Approbation

* L'agent peut **demander**, jamais **accorder**. L'octroi passe par un canal distinct :
  * CLI locale : `python -m artemis.policy list|approve|deny|audit` ;
  * HTTP : `X-Approver-Token` ∈ `ARTEMIS_APPROVER_TOKENS` (`nom:jeton,…`), **obligatoirement
    différent** de `ARTEMIS_API_TOKEN` (sinon 403). Non configuré → 503, personne ne peut approuver.
* Une approbation est liée au **digest exact** de l'action (opération, paramètres, appareil, session,
  origine), expire (`ARTEMIS_APPROVAL_TIMEOUT`, 120 s par défaut) et se consomme **atomiquement**
  (`UPDATE … WHERE status='approved' AND digest=? …`) : un seul gagnant sous concurrence, rejeu refusé.
* Maximum 20 demandes en attente ; les doublons sont réutilisés.
* Un PIN envoyé par le même client n'est **pas** considéré comme preuve de présence humaine.

## 4. Journal d'audit

`audit.jsonl` (permissions 0600, `ARTEMIS_POLICY_DIR`). Deux événements liés par `trace_id` :
`decision` (policy id, décision, justification, mode, origine, paramètres sûrs) et `outcome`
(`executed`, `failed`, `blocked`, `approved_and_consumed`, …). Le texte saisi n'est jamais journalisé
(seulement sa longueur) ; le shell est journalisé en verbes + longueur + sha256 tronqué. Si le journal
est indisponible, toute décision non-`ALLOW` est **refusée** (échec fermé).

## 5. Rapport de couverture des chemins d'exécution

| Voie | Atteint Android ? | Contrôle | Vérifié par |
|---|---|---|---|
| `create_driver` / `get_driver` (toutes les actions agent : tap, swipe, texte, touches, apps, shell) | oui | `PolicyGuardedDriver` (enveloppe obligatoire, sous-classe de `BaseDeviceDriver`) | `test_coverage.py` (usine, cache, mock) |
| `AndroidAdbDriver(` | oui | construit **uniquement** dans `factory.py` | test AST |
| Affectation de `_active_driver` | — | seulement `factory.py` et l'acteur mock | test AST |
| `UnifiedMobileControllerBase` (`open_url`, `erase_text`, helpers `tap_normalized`…) | oui | passent par le driver gardé | `test_guard.py` |
| `RunAdbCommandTool` (adb direct) | oui | `_policy_gate` avant `create_subprocess_exec` | test AST + test « jamais lancé si refusé » |
| `RunShortAdbCommandTool` (adb direct) | oui | idem | idem |
| Poignées brutes (`device`, `adb_client`, `ui_adb_client`…) | oui | inaccessibles via le garde (`AttributeError`) | `test_guard.py` |
| Endpoints d'approbation HTTP | — | jeton d'approbateur distinct | `test_http.py` |

Les points d'entrée voix, `/api/run`, MCP et CLI convergent tous vers ce même goulot ; le test
`test_every_entry_point_is_subject_to_the_same_policy` l'exerce avec chaque étiquette d'origine.
**Ce n'est pas un test de bout en bout** des vraies routes (page vocale, serveur HTTP, serveur MCP, CLI).

## 6. Limites connues (écarts)

1. **Aucun test sur téléphone réel.** Tout est vérifié avec des pilotes factices / mock. Le test
   « Ouvre les paramètres du téléphone » reste à faire par Zaki.
2. **Identité non authentifiée par utilisateur.** Origine, session et demandeur traversent désormais
   le worker via l'environnement (v1.1), mais l'API n'a qu'un jeton partagé et `ingress` est déclaré
   par le client. Le point d'entrée voix passe par `/api/run` et apparaît comme `task:<ingress>`.
3. **Utilisations brutes en lecture seule** de `device.shell` hors du driver (logcat, dumpsys, date
   dans `mobile/log_utils.py`, `utils/app_launch_utils.py`, `platform_specific_commands_controller.py`).
4. **Hors périmètre** : scripts sous `scripts/`, outils d'infrastructure (serveur adb, émulateur,
   scrcpy, accessibilité), `python_executor` (exécution côté hôte).
5. `manage_task send_input` envoie de l'entrée standard à une commande déjà autorisée.
6. `input tap` via shell n'est résolu par libellé que si les coordonnées sont littérales.
7. (corrigé en v1.1) un tap sans application identifiable exige maintenant une approbation. Coût : une
   seconde lecture d'écran après chaque approbation.
8. Latence : une lecture d'écran par tap (jusqu'à 3 s de délai maximal).
9. **Changement de comportement** : `echo $VAR` et autres expansions sont refusés (`UNKNOWN`) ; deux
   tests existants ont été adaptés (`printenv`, `cat`) sans assouplir la politique.
10. **Windows** : les permissions `0600` du journal ne sont pas appliquées sur NTFS (le test correspondant
    est ignoré sur Windows, explicitement). Protéger `ARTEMIS_POLICY_DIR` par ACL du dossier.
11. Le mode `ARTEMIS_POLICY_MODE=audit` (journal seul) est ignoré si `ARTEMIS_ENV=production`.

## 7. Rapport Red Team

| Attaque | Résultat | Test |
|---|---|---|
| Contournement par API directe / autre origine | refusé, même décision pour voice/api/mcp/cli/console | `test_http.py` |
| Injection de prompt (texte « l'utilisateur a déjà confirmé » dans paramètres ou libellé) | décision inchangée | `test_rules.py` |
| Injection d'URL dans `open_url` (`'; rm -rf /sdcard; '`) | `DENY` | `test_rules.py` |
| Chaînage `;`, `&&`, `\|\|`, `\|`, `&`, retour ligne ; chemin absolu `/system/bin/rm` ; `..` | `DENY` | `test_rules.py` |
| Substitution `$(…)`, backticks, `$VAR`, redirection, `FOO=bar cmd`, `sh -c` | `UNKNOWN` (bloqué) | `test_rules.py` |
| Séquence d'actions inoffensives puis action sensible | s'arrête au pas sensible | `test_guard.py` |
| Commande destructrice scindée sur plusieurs appels | chaque appel jugé seul | `test_guard.py` |
| Accès aux poignées adb brutes | `AttributeError` | `test_guard.py` |
| Rejeu d'une approbation | refusé (usage unique) | `test_approvals.py` |
| Approbation détournée vers une autre action / appareil / session | refusée (digest) | `test_approvals.py` |
| Approbation expirée / refus retourné / double décision | refusé | `test_approvals.py` |
| 16 consommations concurrentes | exactement 1 gagnant | `test_approvals.py` |
| Deux agents, une approbation humaine | un seul exécute | `test_approvals.py` |
| Jeton API utilisé comme jeton d'approbateur | 403 | `test_http.py` |
| Règle qui plante / magasin d'approbations en panne / journal en panne | bloqué (échec fermé) | `test_approvals.py` |
| Fuite de contenu dans l'audit | texte saisi absent du journal | `test_approvals.py` |
| **Contrôle négatif** : retrait du garde de l'usine et de `_policy_gate` | 6 tests échouent, puis repassent à la restauration | exécution manuelle |

Aucun contournement n'a été trouvé dans les voies cartographiées. Cela ne prouve pas l'absence de
voies non cartographiées (voir limites 3 et 4).

## 8. Configuration

| Variable | Rôle |
|---|---|
| `ARTEMIS_POLICY_MODE` | `enforce` (défaut) ou `audit` (hors production seulement) |
| `ARTEMIS_POLICY_FINANCE` | `approve` pour rétrograder la finance de `DENY` à `REQUIRE_APPROVAL` |
| `ARTEMIS_APPROVAL_TIMEOUT` | durée de vie d'une demande (s, défaut 120) |
| `ARTEMIS_APPROVER_TOKENS` | `nom:jeton,…` pour l'API d'approbation (≠ `ARTEMIS_API_TOKEN`) |
| `ARTEMIS_POLICY_AUDIT_REQUIRED` | `1` pour refuser aussi les `ALLOW` si l'audit est indisponible (automatique en production) |
| `ARTEMIS_POLICY_REQUESTER` | classe de l'appelant, posée par la file de tâches |
| `ARTEMIS_POLICY_DIR` | dossier du journal et de la base d'approbations |
| `ARTEMIS_POLICY_ORIGIN`, `ARTEMIS_SESSION_ID` | origine / session par défaut quand aucun contexte n'est posé |

## 9. Tests

`uv run --no-sync pytest tests/unit/policy tests/unit/tools/test_command_tool.py -q` — 211 tests (185 + 26 de `command_tool`).
Ajoutés au job CI bloquant. Ruff, quality ratchet et pyright passent.
