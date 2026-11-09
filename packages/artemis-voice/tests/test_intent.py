import pytest

from artemis_voice.intent import (
    IntentKind,
    is_sensitive,
    parse_confirmation,
    parse_intent,
    strip_wake_word,
)

WAKE = ("artemis",)


def test_wake_word_is_stripped_accent_insensitively():
    intent = parse_intent("Artémis, ouvre les Paramètres", wake_words=WAKE)
    assert intent.kind is IntentKind.TASK
    assert intent.goal == "ouvre les Paramètres"
    assert intent.heard_wake_word


def test_hey_prefix_and_no_wake_word():
    assert strip_wake_word("hey Artemis open Chrome", WAKE) == ("open Chrome", True)
    assert strip_wake_word("open Chrome", WAKE) == ("open Chrome", False)


def test_require_wake_word_ignores_ambient_speech():
    ambient = parse_intent("ouvre WhatsApp", wake_words=WAKE, require_wake_word=True)
    assert ambient.kind is IntentKind.EMPTY
    addressed = parse_intent("Artemis ouvre WhatsApp", wake_words=WAKE, require_wake_word=True)
    assert addressed.kind is IntentKind.TASK


@pytest.mark.parametrize(
    "phrase", ["stop", "Arrête", "Annule tout", "Artemis, stop", "ça suffit", "cancel", "وقف"]
)
def test_stop_always_honoured_even_when_wake_word_required(phrase):
    intent = parse_intent(phrase, wake_words=WAKE, require_wake_word=True)
    assert intent.kind is IntentKind.STOP


@pytest.mark.parametrize("phrase", ["quitter", "Au revoir", "goodbye"])
def test_exit(phrase):
    assert parse_intent(phrase, wake_words=WAKE).kind is IntentKind.EXIT


def test_stop_word_inside_a_task_is_not_a_stop():
    intent = parse_intent(
        "ouvre le chronomètre et fais stop au bout de 10 secondes", wake_words=WAKE
    )
    assert intent.kind is IntentKind.TASK


def test_empty():
    assert parse_intent("  ...  ", wake_words=WAKE).kind is IntentKind.EMPTY


@pytest.mark.parametrize(
    "phrase",
    [
        "envoie un message à Paul",
        "Supprime cette photo",
        "paye la facture",
        "send 20 dollars to Sam",
        "delete all the photos",
        "ouvre les paramètres puis mets mon mot de passe",
        "ارسل رسالة",
        "حذف الصور",
    ],
)
def test_sensitive_actions_detected(phrase):
    assert is_sensitive(phrase)


@pytest.mark.parametrize(
    "phrase", ["ouvre les paramètres Wi-Fi", "open Chrome", "vérifie le pourcentage de batterie"]
)
def test_harmless_actions_not_sensitive(phrase):
    assert not is_sensitive(phrase)


def test_extra_sensitive_words():
    assert not is_sensitive("lance le jeu")
    assert is_sensitive("lance le jeu", ("lance",))


def test_task_carries_sensitive_flag():
    assert parse_intent("Artemis envoie un SMS", wake_words=WAKE).sensitive is True
    assert parse_intent("Artemis ouvre Gmail", wake_words=WAKE).sensitive is False


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("oui", True),
        ("Oui, vas-y", True),
        ("yes", True),
        ("d'accord", True),
        ("نعم", True),
        ("non", False),
        ("no", False),
        ("annule", False),
        ("oui non", False),
        ("لا", False),
        ("oui mais envoie-le à Paul", None),
        ("peut-être", None),
        ("", None),
    ],
)
def test_confirmation(answer, expected):
    assert parse_confirmation(answer) is expected


@pytest.mark.parametrize("phrase", ["oui", "non", "ok", "yes", "Artemis, oui", "d'accord", "نعم"])
def test_lone_yes_or_no_is_never_a_task(phrase):
    assert parse_intent(phrase, wake_words=WAKE).kind is IntentKind.EMPTY


def test_yes_inside_a_real_command_is_still_a_task():
    assert (
        parse_intent("dis oui à l'invitation sur WhatsApp", wake_words=WAKE).kind is IntentKind.TASK
    )
