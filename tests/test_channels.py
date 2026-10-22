from iptv.channels import channel_key, display_name, merge
from iptv.m3u import Entry


def test_display_name_drops_alternate_counter():
    assert display_name("Россия 1 (2)") == "Россия 1"
    assert display_name("Матч ТВ [3]") == "Матч ТВ"
    assert display_name("Канал 2") == "Канал 2"


def test_channel_key_ignores_quality_and_punctuation():
    assert channel_key("Первый канал HD") == channel_key("первый канал")
    assert channel_key("НТВ FHD (2)") == channel_key("НТВ")
    assert channel_key("Ёлки") == channel_key("Елки")
    assert channel_key("1+1 Ukraina") != channel_key("1 Ukraina")


def test_merge_collects_fallback_urls_in_source_order():
    entries = [
        Entry(name="Россия 1", url="https://a/1", group="Основные", tvg_id="rossia1"),
        Entry(name="Россия 1 (2)", url="https://b/1", group="Основные", logo="https://logo/r1.png"),
        Entry(name="Россия 1 HD", url="https://a/1", group="Основные"),
        Entry(name="НТВ", url="https://a/ntv", group="Основные"),
    ]

    channels = merge(entries)

    assert [c.name for c in channels] == ["Россия 1", "НТВ"]
    rossia = channels[0]
    assert rossia.urls == ("https://a/1", "https://b/1")
    assert rossia.tvg_id == "rossia1"
    assert rossia.logo == "https://logo/r1.png"


def test_merge_keeps_first_group():
    channels = merge([
        Entry(name="Кино 1", url="https://a/1", group="Кинозалы"),
        Entry(name="Кино 1", url="https://b/1", group="Фильмы"),
    ])

    assert channels[0].group == "Кинозалы"


def test_merge_falls_back_to_url_key_for_unnamed_entries():
    channels = merge([Entry(name="!!!", url="https://a/1"), Entry(name="???", url="https://a/2")])

    assert len(channels) == 2
