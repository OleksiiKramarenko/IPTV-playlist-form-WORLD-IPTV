import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from iptv.checker import check_urls, next_uri_of

MASTER = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nmedia.m3u8\n"
MEDIA = "#EXTM3U\n#EXTINF:4,\nold.ts\n#EXTINF:4,\nnew.ts\n"


def respond(**kwargs):
    async def handler(_):
        return web.Response(**kwargs)
    return handler


def playlist(body: str):
    return respond(text=body, content_type="application/vnd.apple.mpegurl")


def build_app() -> web.Application:
    async def slow(_):
        await asyncio.sleep(2)
        return web.Response(body=b"late")

    async def redirect(_):
        raise web.HTTPFound("/master.m3u8")

    app = web.Application()
    app.router.add_get("/master.m3u8", playlist(MASTER))
    app.router.add_get("/media.m3u8", playlist(MEDIA))
    app.router.add_get("/new.ts", respond(body=b"\x47" * 4096))
    app.router.add_get("/old.ts", respond(status=404))
    app.router.add_get("/stale.m3u8", playlist("#EXTM3U\n#EXTINF:4,\nnew.ts\n#EXTINF:4,\nold.ts\n"))
    app.router.add_get("/empty.m3u8", playlist("#EXTM3U\n"))
    app.router.add_get("/loop.m3u8", playlist("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nloop.m3u8\n"))
    app.router.add_get("/page", respond(text="<html></html>", content_type="text/html"))
    app.router.add_get("/redirect", redirect)
    app.router.add_get("/slow", slow)
    return app


def run_checks(paths: list[str], timeout: float = 1.0) -> dict[str, bool]:
    async def scenario():
        async with TestServer(build_app()) as server:
            urls = [str(server.make_url(p)) for p in paths]
            results = await check_urls(urls + urls[:1], timeout=timeout, workers=4)
            return {p: results[u].ok for p, u in zip(paths, urls)}

    return asyncio.run(scenario())


def test_follows_master_and_media_playlist_to_newest_segment():
    results = run_checks(["/master.m3u8", "/redirect", "/new.ts"])

    assert results == {"/master.m3u8": True, "/redirect": True, "/new.ts": True}


def test_rejects_unplayable_streams():
    results = run_checks(["/stale.m3u8", "/empty.m3u8", "/loop.m3u8", "/page", "/old.ts", "/missing", "/slow"])

    assert results == dict.fromkeys(results, False)


def test_next_uri_of():
    assert next_uri_of(MASTER) == "media.m3u8"
    assert next_uri_of(MEDIA) == "new.ts"
    assert next_uri_of("#EXTM3U\n") is None
