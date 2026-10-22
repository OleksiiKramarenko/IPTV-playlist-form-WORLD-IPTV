import asyncio
import gzip

from aiohttp import web
from aiohttp.test_utils import TestServer

from iptv.epg import GuideChannel, build_index, enrich, fetch_index, parse_channels
from iptv.m3u import Entry

XMLTV = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE tv SYSTEM "https://example.com/xmltv.dtd">
<tv>
<channel id="inter-ua"><display-name>Интер</display-name><display-name>Iнтер</display-name><icon src="https://logo/inter.png"/></channel>
<channel id="5kanal-ua"><display-name>5 канал [UA]</display-name><display-name>5 канал (Украина)</display-name></channel>
<channel id="5kanal-kz"><display-name>5 канал [KZ]</display-name></channel>
<channel id="pervy"><display-name>Первый канал</display-name><icon src="https://logo/pervy.png"/></channel>
<programme channel="pervy" start="20260101000000 +0000"><title>News</title></programme>
<channel id="after-programmes"><display-name>Never read</display-name></channel>
</tv>
""".encode()


def chunked(data: bytes, size: int = 50):
    return [data[i:i + size] for i in range(0, len(data), size)]


def test_parse_channels_stops_at_first_programme_plain_and_gzip():
    for payload in (XMLTV, gzip.compress(XMLTV)):
        channels = parse_channels(chunked(payload))

        assert [c.id for c in channels] == ["inter-ua", "5kanal-ua", "5kanal-kz", "pervy"]
        assert channels[0] == GuideChannel(id="inter-ua", names=("Интер", "Iнтер"), icon="https://logo/inter.png")


def test_build_index_prefers_first_guide_for_names_and_icons():
    first = parse_channels([XMLTV])
    second = [
        GuideChannel(id="inter-other", names=("Интер",), icon="https://logo/other.png"),
        GuideChannel(id="pervy", names=("Первый",), icon="https://logo/late.png"),
    ]

    index = build_index([first, second])

    assert index.by_name["интер"] == "inter-ua"
    assert index.by_name["5каналукраина"] == "5kanal-ua"
    assert index.by_name["первый"] == "pervy"
    assert index.icons["pervy"] == "https://logo/pervy.png"
    assert "inter-other" in index


def test_ambiguous_tagless_name_is_not_indexed():
    index = build_index([parse_channels([XMLTV])])

    assert "5канал" not in index.by_name
    assert index.by_name["5каналua"] == "5kanal-ua"


def test_enrich_keeps_known_ids_uses_aliases_names_and_skips_groups():
    index = build_index([parse_channels([XMLTV])])
    entries = [
        Entry(name="Первый канал HD", url="u1", group="Основные", tvg_id="pervy", logo="https://own/logo.png"),
        Entry(name="Inter", url="u2", group="Украина", tvg_id="Inter.ua"),
        Entry(name="Интер", url="u3", group="Основные"),
        Entry(name="Неизвестный", url="u4", group="Основные", tvg_id="unknown"),
        Entry(name="Интер", url="u5", group="Фильмы"),
    ]

    result = enrich(entries, index, aliases={"Inter": "inter-ua"}, skip_groups=["Фильмы"])

    assert [(e.tvg_id, e.logo) for e in result] == [
        ("pervy", "https://own/logo.png"),
        ("inter-ua", "https://logo/inter.png"),
        ("inter-ua", "https://logo/inter.png"),
        ("unknown", ""),
        ("", ""),
    ]
    assert entries[1].tvg_id == "Inter.ua"


def test_fetch_index_reads_guides_and_survives_failures():
    async def guide(_):
        return web.Response(body=gzip.compress(XMLTV))

    async def scenario():
        app = web.Application()
        app.router.add_get("/epg.xml.gz", guide)
        async with TestServer(app) as server:
            return await fetch_index([str(server.make_url("/epg.xml.gz")), str(server.make_url("/missing.xml"))])

    index = asyncio.run(scenario())

    assert "inter-ua" in index and "after-programmes" not in index
