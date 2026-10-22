import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from iptv.__main__ import main
from iptv.config import Source
from iptv.sources import fetch_sources

GOOD = '#EXTM3U\n#EXTINF:-1 group-title="Основные",Первый\nhttps://a/1\n'


def build_app() -> web.Application:
    async def good(_):
        return web.Response(text=GOOD)

    async def empty(_):
        return web.Response(text="#EXTM3U\n")

    app = web.Application()
    app.router.add_get("/good.m3u", good)
    app.router.add_get("/empty.m3u", empty)
    return app


def test_fetch_sources_reports_each_source_independently():
    async def scenario():
        async with TestServer(build_app()) as server:
            sources = [Source(name=n, url=str(server.make_url(f"/{n}.m3u"))) for n in ("good", "empty", "missing")]
            return await fetch_sources(sources)

    good, empty, missing = asyncio.run(scenario())

    assert [e.name for e in good.entries] == ["Первый"] and good.error is None
    assert empty.entries == () and empty.error == "no entries"
    assert missing.entries == () and "404" in missing.error


def test_cli_returns_error_code_on_bad_config(tmp_path, caplog):
    assert main(["--config", str(tmp_path / "missing.json")]) == 1
    assert "not found" in caplog.text
