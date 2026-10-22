import pytest

from iptv.config import GroupRule
from iptv.groups import classify, normalize_group

RULES = (
    GroupRule(name="Кинозалы", match=("кинозал",)),
    GroupRule(name="Фильмы", match=("kino zal",)),
    GroupRule(name="Основные", match=("основные", "общественные")),
)
EXCLUDE = ("vpn", "регионал", "18+")


def test_normalize_strips_emoji_and_case():
    assert normalize_group("🔴 Кинозалы 🔴") == "кинозалы"
    assert normalize_group("  KINO_ZAL!! ") == "kino zal"


@pytest.mark.parametrize(
    "title, expected",
    [
        ("🔴 Кинозалы 🔴", "Кинозалы"),
        ("Кинозалы Kinowalk", "Кинозалы"),
        ("KINO ZAL", "Фильмы"),
        ("Общественные", "Основные"),
        ("KINO ZAL (VPN)", None),
        ("Основные (региональные)", None),
        ("Взрослые 18+", None),
        ("Бенедикт Камбербэтч", None),
        ("", None),
    ],
)
def test_classify(title, expected):
    assert classify(title, RULES, EXCLUDE) == expected


def test_first_matching_rule_wins():
    rules = (GroupRule(name="A", match=("кино",)), GroupRule(name="B", match=("кинозал",)))

    assert classify("Кинозал", rules, ()) == "A"
