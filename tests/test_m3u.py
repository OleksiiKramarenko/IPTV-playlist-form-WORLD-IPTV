from iptv import m3u

SAMPLE = """#EXTM3U url-tvg="https://epg.example/a.xml"
#EXTINF:-1 group-title="Основные" tvg-id="pervy" tvg-logo="https://logo.example/1.png", Первый
https://a.example/1.m3u8
#EXTINF:-1 tvg-id="ntv" group-title="Общественные" ,НТВ HD
#EXTVLCOPT:http-user-agent=Foo
https://a.example/ntv.m3u8

#EXTINF:-1,Без группы, с запятой
#EXTGRP:Кино
https://a.example/kino.m3u8
#EXTINF:-1 group-title="Orphan",Без ссылки
#EXTINF:-1 group-title="Sport",Спорт
https://a.example/sport.m3u8
"""


def test_parse_extracts_attributes_and_title():
    entries = m3u.parse(SAMPLE)

    assert entries[0] == m3u.Entry(
        name="Первый", url="https://a.example/1.m3u8", group="Основные",
        logo="https://logo.example/1.png", tvg_id="pervy",
    )
    assert entries[1].name == "НТВ HD"
    assert entries[1].url == "https://a.example/ntv.m3u8"


def test_parse_uses_extgrp_and_keeps_commas_in_title():
    entry = m3u.parse(SAMPLE)[2]

    assert entry.name == "Без группы, с запятой"
    assert entry.group == "Кино"


def test_parse_drops_extinf_without_url():
    names = [e.name for e in m3u.parse(SAMPLE)]

    assert "Без ссылки" not in names
    assert names[-1] == "Спорт"


def test_is_vod_file_ignores_query_string():
    assert m3u.is_vod_file("https://x.example/film.MP4?token=1")
    assert not m3u.is_vod_file("https://x.example/live/index.m3u8?mp4=1")


def test_render_roundtrip_and_escapes_quotes():
    entries = [m3u.Entry(name='Канал "1"', url="https://a.example/1.m3u8", group="Основные", tvg_id="c1")]

    text = m3u.render(entries, ["https://epg.example/a.xml", "https://epg.example/b.xml"])

    assert text.startswith('#EXTM3U url-tvg="https://epg.example/a.xml,https://epg.example/b.xml"\n')
    assert "tvg-name=\"Канал '1'\"" in text
    parsed = m3u.parse(text)
    assert parsed[0].url == "https://a.example/1.m3u8"
    assert parsed[0].group == "Основные"


def test_render_without_epg_has_plain_header():
    assert m3u.render([]) == "#EXTM3U\n"
