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

"""Deterministic policy rules.

Pure functions: an :class:`~artemis.policy.model.Action` goes in, a
:class:`~artemis.policy.model.Verdict` comes out. Nothing here reads model text, prompts
or goals, so injected instructions cannot change a decision. Anything the rules cannot
classify is ``UNKNOWN`` and is blocked (fail closed).
"""

from __future__ import annotations

import os
import posixpath
import re

from artemis.policy.model import Action, Decision, Effect, Verdict

POLICY_VERSION = "v1"

# --------------------------------------------------------------------------- classes
FINANCE_PACKAGE = re.compile(
    r"(bank|banque|desjardins|rbc|\btd\b|bmo|scotia|cibc|tangerine|paypal|venmo|cashapp|cash\.app"
    r"|wallet|\.pay\b|\.pay\.|payment|revolut|wise|coinbase|binance|crypto|interac|zelle|stripe"
    r"|nbu\.paisa|moneytransfer|western\.?union)",
    re.I,
)
COMM_PACKAGE = re.compile(
    r"(messag|\.mms|\.sms|telephony|dialer|telecom|whatsapp|telegram|signal|gmail|outlook|\.mail"
    r"|facebook|instagram|twitter|\.x\b|linkedin|snapchat|tiktok|discord|slack|teams|viber|wechat)",
    re.I,
)
COMMERCE_PACKAGE = re.compile(r"(com\.android\.vending|amazon|ebay|aliexpress|shopify|etsy)", re.I)

FINANCE_LABEL = re.compile(
    r"\b(pay|payer|payment|paiement|purchase|buy|acheter|achat|checkout|commander|order now"
    r"|place order|transfer|virement|transf[ée]rer|send money|donate|donner|subscribe|abonner"
    r"|abonnement|confirm payment|withdraw|retrait)\b|ادفع|دفع|شراء|تحويل|اشتر",
    re.I,
)
DESTROY_LABEL = re.compile(
    r"\b(delete|supprimer|suppression|remove|retirer|erase|effacer|wipe|reset|r[ée]initialiser"
    r"|uninstall|d[ée]sinstaller|clear data|vider|empty|discard|format)\b|حذف|مسح|إزالة|إعادة ضبط",
    re.I,
)
COMM_LABEL = re.compile(
    r"\b(send|envoyer|envoi|post|publier|publish|tweet|reply|r[ée]pondre|share|partager|call"
    r"|appeler|submit|soumettre|forward|invite|inviter)\b|إرسال|ارسال|نشر|اتصال|مشاركة",
    re.I,
)

# Android keycodes by name and number.
SAFE_KEYS = {
    "home", "back", "app_switch", "volume_up", "volume_down", "delete", "tab", "menu", "search",
    "escape", "dpad_up", "dpad_down", "dpad_left", "dpad_right", "dpad_center", "page_up",
    "page_down", "move_home", "move_end", "power", "endcall",
}  # fmt: skip
SUBMIT_KEYS = {"enter", "numpad_enter"}
APPROVAL_KEYS = {"call"}
SAFE_KEYCODES = {
    3,
    4,
    6,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    61,
    67,
    82,
    84,
    111,
    122,
    123,
    187,
    92,
    93,
    279,
}
SUBMIT_KEYCODES = {66, 160}
APPROVAL_KEYCODES = {5}

# --------------------------------------------------------------------------- shell
ALWAYS_DENIED = {
    "su", "reboot", "poweroff", "shutdown", "mkfs", "dd", "mount", "umount", "insmod", "rmmod",
    "setenforce", "resetprop", "magisk", "nc", "ncat", "telnet", "wget", "curl", "tcpdump",
    "run-as", "sqlite3", "iptables", "ip6tables", "format", "wipe",
}  # fmt: skip
OPAQUE_WRAPPERS = {
    "sh", "bash", "ash", "zsh", "eval", "exec", "xargs", "busybox", "nohup", "timeout", "env",
    "time", "command", "source", ".", "toybox", "setsid", "script", "python", "python3", "perl",
}  # fmt: skip
READ_ONLY = {
    "getprop", "date", "uname", "id", "whoami", "pwd", "echo", "printf", "cat", "ls", "stat", "wc",
    "head", "tail", "grep", "egrep", "df", "ps", "uptime", "sleep", "true", "false", "test", "[",
    "dumpsys", "screencap", "screenrecord", "uiautomator", "cd", "export", "readlink", "basename",
    "dirname", "sort", "uniq", "tr", "cut", "seq", "which", "type", "hostname", "printenv",
}  # fmt: skip
SCRATCH_PREFIXES = ("/data/local/tmp/",)
PROTECTED_ROOTS = {
    "/",
    "/sdcard",
    "/storage",
    "/data",
    "/system",
    "/vendor",
    "/product",
    "/etc",
    "/sdcard/",
}
SAFE_VIEW_ACTIONS = {
    "android.intent.action.VIEW", "android.intent.action.MAIN", "android.intent.action.DIAL",
    "android.intent.action.SEARCH", "android.intent.action.WEB_SEARCH",
}  # fmt: skip
COMM_INTENTS = {
    "android.intent.action.SENDTO", "android.intent.action.SEND", "android.intent.action.SEND_MULTIPLE",
    "android.intent.action.CALL",
}  # fmt: skip
DESTROY_INTENTS = {"android.intent.action.DELETE", "android.intent.action.UNINSTALL_PACKAGE"}


def _v(decision: Decision, policy_id: str, reason: str, *effects: Effect) -> Verdict:
    return Verdict(decision, f"{POLICY_VERSION}/{policy_id}", reason, effects)


def finance_decision() -> Decision:
    """Finance is DENY by default; ``ARTEMIS_POLICY_FINANCE=approve`` downgrades to approval."""
    return (
        Decision.REQUIRE_APPROVAL
        if os.environ.get("ARTEMIS_POLICY_FINANCE", "").lower() == "approve"
        else Decision.DENY
    )


def package_class(package: str | None) -> str:
    if not package:
        return "unknown"
    if FINANCE_PACKAGE.search(package):
        return "finance"
    if COMM_PACKAGE.search(package):
        return "comm"
    if COMMERCE_PACKAGE.search(package):
        return "commerce"
    return "neutral"


# --------------------------------------------------------------------------- shell parsing
class ShellParse:
    def __init__(self) -> None:
        self.segments: list[list[str]] = []
        self.problems: list[str] = []


def parse_shell(command: str) -> ShellParse:
    """Split a shell line into command segments without executing or expanding anything."""
    out = ShellParse()
    cur: list[str] = []
    tok: list[str] = []
    in_tok = False
    quote = ""
    i, n = 0, len(command)

    def end_token() -> None:
        nonlocal in_tok
        if in_tok:
            cur.append("".join(tok))
            tok.clear()
            in_tok = False

    def end_segment() -> None:
        end_token()
        if cur:
            out.segments.append(list(cur))
            cur.clear()

    while i < n:
        c = command[i]
        if quote == "'":
            if c == "'":
                quote = ""
            else:
                tok.append(c)
            i += 1
            continue
        if quote == '"':
            if c == "\\" and i + 1 < n:
                tok.append(command[i + 1])
                i += 2
                continue
            if c == '"':
                quote = ""
            elif c == "`" or (c == "$" and i + 1 < n and command[i + 1] in "({"):
                out.problems.append("command substitution")
                tok.append(c)
            elif c == "$":
                out.problems.append("variable expansion")
                tok.append(c)
            else:
                tok.append(c)
            i += 1
            continue
        # unquoted
        if c in "'\"":
            quote = c
            in_tok = True
            i += 1
        elif c == "\\" and i + 1 < n:
            tok.append(command[i + 1])
            in_tok = True
            i += 2
        elif c == "`" or c == "$":
            out.problems.append(
                "command substitution"
                if c == "`" or command[i + 1 : i + 2] == "("
                else "variable expansion"
            )
            tok.append(c)
            in_tok = True
            i += 1
        elif c in ";\n":
            end_segment()
            i += 1
        elif c in "&|":
            end_segment()
            i += 2 if command[i + 1 : i + 2] == c else 1
        elif c in "<>":
            end_token()
            j = i
            while j < n and command[j] in "<>&":
                j += 1
            rest = command[j:].lstrip()
            target = rest.split(None, 1)[0] if rest else ""
            # allow only: >/dev/null, 2>/dev/null, 2>&1
            fd_dup = command[i:j].endswith("&") and target[:1].isdigit()
            if target != "/dev/null" and not (command[i:j] == ">&" and fd_dup) and not fd_dup:
                out.problems.append("redirection")
            i = j + (len(command[j:]) - len(rest)) + len(target)
        elif c.isspace():
            end_token()
            i += 1
        else:
            tok.append(c)
            in_tok = True
            i += 1
    if quote:
        out.problems.append("unterminated quote")
    end_segment()
    return out


# --------------------------------------------------------------------------- UI rules
def _ui_contextual(action: Action, what: str, *, targeted: bool = True) -> Verdict:
    """Taps, long presses, swipes, Enter: decided by what is under the finger."""
    label = (action.target_label or "").strip()
    pkg_cls = package_class(action.foreground_package)
    if label:
        if FINANCE_LABEL.search(label):
            return _v(
                finance_decision(),
                "ui.finance_label",
                f"{what} on a payment control",
                Effect.FINANCE,
            )
        if DESTROY_LABEL.search(label):
            return _v(
                Decision.REQUIRE_APPROVAL,
                "ui.destroy_label",
                f"{what} on a destructive control",
                Effect.DESTROY,
            )
        if COMM_LABEL.search(label):
            return _v(
                Decision.REQUIRE_APPROVAL,
                "ui.comm_label",
                f"{what} on a send/publish control",
                Effect.COMMUNICATE,
            )
    if pkg_cls == "finance":
        return _v(
            Decision.REQUIRE_APPROVAL,
            "ui.finance_app",
            f"{what} inside a financial app",
            Effect.FINANCE,
        )
    if targeted:
        # A pointed action whose target we could not identify must not pass as "navigation".
        if not action.ui_known:
            return _v(
                Decision.REQUIRE_APPROVAL,
                "ui.context_unavailable",
                f"{what}: screen content could not be read, target unknown",
                Effect.UNCLASSIFIED,
            )
        if action.foreground_package is None:
            return _v(
                Decision.REQUIRE_APPROVAL,
                "ui.package_unknown",
                f"{what}: foreground app unknown",
                Effect.UNCLASSIFIED,
            )
        if not label and pkg_cls in {"comm", "commerce"}:
            return _v(
                Decision.REQUIRE_APPROVAL,
                "ui.unlabeled_in_sensitive_app",
                f"{what} on an unlabeled control inside a messaging/shopping app",
                Effect.COMMUNICATE,
            )
    return _v(
        Decision.ALLOW, "ui.navigate", f"{what} (no sensitive control detected)", Effect.NAVIGATE
    )


def _key_verdict(key: object, action: Action) -> Verdict:
    name = str(key).lower().removeprefix("keycode_")
    code: int | None = int(name) if name.lstrip("-").isdigit() else None
    submit = name in SUBMIT_KEYS or (code in SUBMIT_KEYCODES)
    if submit:
        cls = package_class(action.foreground_package)
        if action.foreground_package is None or cls in {"comm", "finance", "commerce"}:
            return _v(
                Decision.REQUIRE_APPROVAL, "key.submit_sensitive_app",
                "Enter can send or confirm inside this app", Effect.COMMUNICATE,
            )  # fmt: skip
        return _v(Decision.ALLOW, "key.submit", "Enter in a neutral app", Effect.INPUT)
    if name in APPROVAL_KEYS or code in APPROVAL_KEYCODES:
        return _v(
            Decision.REQUIRE_APPROVAL,
            "key.call",
            "key press starts a phone call",
            Effect.COMMUNICATE,
        )
    if name in SAFE_KEYS or code in SAFE_KEYCODES:
        return _v(Decision.ALLOW, "key.safe", "navigation key", Effect.NAVIGATE)
    return _v(
        Decision.UNKNOWN, "key.unclassified", f"unclassified key {name!r}", Effect.UNCLASSIFIED
    )


# --------------------------------------------------------------------------- shell rules
def _path_verdict(paths: list[str], verb: str, destructive: bool) -> Verdict:
    if not paths:
        return _v(
            Decision.REQUIRE_APPROVAL,
            "shell.no_target",
            f"{verb} without an explicit target",
            Effect.DESTROY,
        )
    worst = _v(
        Decision.ALLOW, "shell.scratch", f"{verb} inside the scratch directory", Effect.MODIFY
    )
    for raw in paths:
        if "*" in raw or "?" in raw or not raw.startswith("/"):
            candidate = _v(
                Decision.REQUIRE_APPROVAL,
                "shell.relative_or_glob",
                f"{verb} with relative path or wildcard",
                Effect.DESTROY,
            )
        else:
            norm = posixpath.normpath(raw)
            if norm in PROTECTED_ROOTS or norm.rstrip("/") in PROTECTED_ROOTS:
                candidate = _v(
                    Decision.DENY,
                    "shell.protected_root",
                    f"{verb} on a protected root {norm}",
                    Effect.DESTROY,
                )
            elif any((norm + "/").startswith(p) for p in SCRATCH_PREFIXES) and norm not in {
                p.rstrip("/") for p in SCRATCH_PREFIXES
            }:
                continue
            else:
                candidate = _v(
                    Decision.REQUIRE_APPROVAL,
                    "shell.outside_scratch",
                    f"{verb} outside the scratch directory",
                    Effect.DESTROY,
                )
        worst = Verdict.worst([worst, candidate])
    return worst


def _intent_verdict(argv: list[str], action: Action) -> Verdict:
    sub = argv[1] if len(argv) > 1 else ""
    if sub in {"force-stop", "kill"}:
        return _v(Decision.ALLOW, "am.stop", "stopping an app is reversible", Effect.MODIFY)
    if sub not in {"start", "start-activity", "startservice"} or sub == "startservice":
        return _v(
            Decision.UNKNOWN, "am.unclassified", f"am {sub} is not classified", Effect.UNCLASSIFIED
        )
    flags: dict[str, str] = {}
    i = 2
    while i < len(argv):
        if argv[i] in {"-a", "-d", "-n", "-p", "-t", "-c"} and i + 1 < len(argv):
            flags[argv[i]] = argv[i + 1]
            i += 2
        else:
            i += 1
    act = flags.get("-a", "")
    data = flags.get("-d", "")
    comp = flags.get("-n", "") or flags.get("-p", "")
    if act in COMM_INTENTS:
        return _v(
            Decision.REQUIRE_APPROVAL,
            "am.communicate",
            f"{act.rsplit('.', 1)[-1]} intent",
            Effect.COMMUNICATE,
        )
    if act in DESTROY_INTENTS:
        return _v(
            Decision.REQUIRE_APPROVAL,
            "am.destroy",
            f"{act.rsplit('.', 1)[-1]} intent",
            Effect.DESTROY,
        )
    if comp and package_class(comp.split("/")[0]) == "finance":
        return _v(finance_decision(), "am.finance_app", "launching a financial app", Effect.FINANCE)
    if act in SAFE_VIEW_ACTIONS or act.startswith("android.settings.") or (not act and comp):
        if (
            act == "android.intent.action.VIEW"
            and data
            and not re.match(r"^(https?|geo|market|content)://?", data, re.I)
        ):
            if re.match(r"^(sms|smsto|mms|mmsto|tel|mailto)", data, re.I):
                return _v(
                    Decision.ALLOW,
                    "am.compose",
                    "opens a composer without sending",
                    Effect.NAVIGATE,
                )
            return _v(
                Decision.UNKNOWN,
                "am.view_scheme",
                "VIEW with an unclassified URI scheme",
                Effect.UNCLASSIFIED,
            )
        return _v(Decision.ALLOW, "am.navigate", "opens an app or page", Effect.NAVIGATE)
    return _v(
        Decision.UNKNOWN,
        "am.unclassified",
        f"intent {act or '?'} is not classified",
        Effect.UNCLASSIFIED,
    )


def _segment_verdict(argv: list[str], action: Action) -> Verdict:
    head = posixpath.basename(argv[0])
    if "=" in head:
        return _v(
            Decision.UNKNOWN,
            "shell.env_prefix",
            "inline environment assignment",
            Effect.UNCLASSIFIED,
        )
    if head in ALWAYS_DENIED:
        return _v(Decision.DENY, "shell.denied_binary", f"{head} is never allowed", Effect.SYSTEM)
    if head in OPAQUE_WRAPPERS:
        return _v(
            Decision.UNKNOWN,
            "shell.opaque_wrapper",
            f"{head} hides the real command",
            Effect.UNCLASSIFIED,
        )
    args = argv[1:]
    if head in {"rm", "rmdir", "unlink", "shred"}:
        paths = [a for a in args if not a.startswith("-")]
        return _path_verdict(paths, head, True)
    if head in {"mkdir", "touch", "cp", "mv", "chmod", "ln", "tee", "truncate"}:
        paths = [a for a in args if not a.startswith("-")]
        return _path_verdict(paths, head, False)
    if head == "input":
        sub = args[0] if args else ""
        if sub in {"tap", "touchscreen"} or sub == "swipe" or sub == "draganddrop":
            nums = [a for a in args[1:] if re.fullmatch(r"-?\d+", a)]
            if sub == "tap" and len(nums) >= 2:
                label = action.labels.get((int(nums[0]), int(nums[1])))
                tmp = Action(
                    "tap", action.device_id, {}, action.foreground_package, label, action.context,
                    ui_known=action.ui_known,
                )  # fmt: skip
                return _ui_contextual(tmp, "tap")
            return _ui_contextual(action, sub, targeted=False)
        if sub == "text":
            return _text_verdict(action)
        if sub == "keyevent":
            codes = [a for a in args[1:] if not a.startswith("-")]
            if not codes:
                return _v(
                    Decision.UNKNOWN,
                    "key.unclassified",
                    "keyevent without a code",
                    Effect.UNCLASSIFIED,
                )
            return Verdict.worst([_key_verdict(c, action) for c in codes])
        if sub == "keycombination":
            if [a for a in args[1:] if not a.startswith("-")] == ["113", "29"]:
                return _v(Decision.ALLOW, "key.select_all", "Ctrl+A", Effect.INPUT)
            return _v(
                Decision.UNKNOWN,
                "key.combination",
                "unclassified key combination",
                Effect.UNCLASSIFIED,
            )
        return _v(
            Decision.UNKNOWN,
            "input.unclassified",
            f"input {sub} is not classified",
            Effect.UNCLASSIFIED,
        )
    if head == "am":
        return _intent_verdict(argv, action)
    if head == "monkey":
        pkgs = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-p"]
        if any(package_class(p) == "finance" for p in pkgs):
            return _v(
                finance_decision(),
                "monkey.finance_app",
                "launching a financial app",
                Effect.FINANCE,
            )
        return _v(Decision.ALLOW, "monkey.launch", "app launch", Effect.NAVIGATE)
    if head in {"pm", "cmd"}:
        sub = args[0] if args else ""
        if head == "cmd" and sub == "package":
            sub = args[1] if len(args) > 1 else ""
        if sub in {
            "list",
            "path",
            "dump",
            "resolve-activity",
            "get-install-location",
            "has-feature",
        }:
            return _v(Decision.ALLOW, "pm.read", "package query", Effect.READ)
        if sub in {
            "uninstall",
            "clear",
            "disable",
            "disable-user",
            "hide",
            "suspend",
            "revoke",
            "trim-caches",
        }:
            return _v(Decision.REQUIRE_APPROVAL, "pm.destroy", f"pm {sub}", Effect.DESTROY)
        if sub in {"install", "grant", "enable", "unhide", "set-installer"}:
            return _v(Decision.REQUIRE_APPROVAL, "pm.modify", f"pm {sub}", Effect.MODIFY)
        return _v(
            Decision.UNKNOWN,
            "pm.unclassified",
            f"{head} {sub} is not classified",
            Effect.UNCLASSIFIED,
        )
    if head == "settings":
        sub = args[0] if args else ""
        if sub in {"get", "list"}:
            return _v(Decision.ALLOW, "settings.read", "settings read", Effect.READ)
        if sub in {"put", "delete"}:
            return _v(
                Decision.REQUIRE_APPROVAL, "settings.modify", f"settings {sub}", Effect.MODIFY
            )
        return _v(
            Decision.UNKNOWN, "settings.unclassified", "settings subcommand", Effect.UNCLASSIFIED
        )
    if head == "svc":
        return _v(
            Decision.REQUIRE_APPROVAL, "svc.modify", "toggles a system service", Effect.SYSTEM
        )
    if head == "wm":
        if len(args) == 1 and args[0] in {"size", "density"}:
            return _v(Decision.ALLOW, "wm.read", "display query", Effect.READ)
        return _v(Decision.REQUIRE_APPROVAL, "wm.modify", "changes display settings", Effect.MODIFY)
    if head == "content":
        sub = args[0] if args else ""
        if sub == "query":
            return _v(
                Decision.REQUIRE_APPROVAL,
                "content.query",
                "reads app data (contacts, messages...)",
                Effect.READ,
            )
        return _v(Decision.DENY, "content.write", f"content {sub} edits app data", Effect.DESTROY)
    if head == "logcat":
        if any(a in {"-c", "-G", "-b"} for a in args) and any(a in {"-c", "-G"} for a in args):
            return _v(
                Decision.REQUIRE_APPROVAL, "logcat.clear", "clears device logs", Effect.DESTROY
            )
        return _v(Decision.ALLOW, "logcat.read", "log read", Effect.READ)
    if head in READ_ONLY:
        return _v(Decision.ALLOW, "shell.read_only", f"{head} is read-only", Effect.READ)
    return _v(
        Decision.UNKNOWN,
        "shell.unclassified",
        f"{head!r} is not a classified command",
        Effect.UNCLASSIFIED,
    )


def _text_verdict(action: Action) -> Verdict:
    cls = package_class(action.foreground_package)
    if cls == "finance":
        return _v(
            Decision.REQUIRE_APPROVAL,
            "input.finance_app",
            "typing inside a financial app",
            Effect.FINANCE,
        )
    return _v(
        Decision.ALLOW, "input.text", "text entry (sending is a separate action)", Effect.INPUT
    )


def evaluate_shell(action: Action) -> Verdict:
    command = str(action.params.get("command", ""))
    parsed = parse_shell(command)
    if not command.strip():
        return _v(Decision.ALLOW, "shell.empty", "empty command", Effect.READ)
    if parsed.problems:
        return _v(
            Decision.UNKNOWN, "shell.unanalyzable",
            "cannot analyze: " + ", ".join(sorted(set(parsed.problems))), Effect.UNCLASSIFIED,
        )  # fmt: skip
    if not parsed.segments:
        return _v(Decision.UNKNOWN, "shell.unparsed", "no command found", Effect.UNCLASSIFIED)
    return Verdict.worst([_segment_verdict(seg, action) for seg in parsed.segments])


# --------------------------------------------------------------------------- entry point
def evaluate(action: Action) -> Verdict:
    """Classify one action. Pure and deterministic."""
    op = action.op
    if op in {
        "get_screen_data",
        "get_current_package",
        "start_video_recording",
        "stop_video_recording",
    }:
        return _v(Decision.ALLOW, "read.observe", "observation only", Effect.READ)
    if op in {"tap", "long_press"}:
        return _ui_contextual(action, op.replace("_", " "))
    if op == "swipe":
        if package_class(action.foreground_package) == "finance":
            return _v(
                Decision.REQUIRE_APPROVAL,
                "ui.finance_app",
                "swipe inside a financial app",
                Effect.FINANCE,
            )
        return _v(Decision.ALLOW, "ui.swipe", "scroll or swipe", Effect.NAVIGATE)
    if op == "input_text":
        return _text_verdict(action)
    if op == "press_key":
        return _key_verdict(action.params.get("key", ""), action)
    if op == "launch_app":
        pkg = str(action.params.get("package", ""))
        if package_class(pkg) == "finance":
            return _v(
                finance_decision(), "launch.finance", "launching a financial app", Effect.FINANCE
            )
        return _v(Decision.ALLOW, "launch.app", "app launch", Effect.NAVIGATE)
    if op == "stop_app":
        return _v(Decision.ALLOW, "stop.app", "stopping an app is reversible", Effect.MODIFY)
    if op == "shell":
        return evaluate_shell(action)
    return _v(
        Decision.UNKNOWN,
        "op.unclassified",
        f"operation {op!r} is not classified",
        Effect.UNCLASSIFIED,
    )
