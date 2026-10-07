"""Request-size guard for multipart photo uploads."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.middleware import RequestBodyLimit


class Recorder:
    def __init__(self):
        self.bodies = []

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.bodies.append(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def client(app):
    guarded = RequestBodyLimit(app, max_bytes=10, path_pattern=r"/upload/[0-9]+")
    return AsyncClient(transport=ASGITransport(guarded), base_url="http://test")


async def test_declared_oversize_is_rejected_without_reading():
    app = Recorder()
    async with client(app) as c:
        response = await c.post("/upload/1", content=b"x" * 11)
    assert response.status_code == 413 and response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert app.bodies == []


async def test_streamed_oversize_stops_reading_at_the_limit():
    app = Recorder()

    async def chunks():
        for _ in range(5):
            yield b"abcd"

    async with client(app) as c:
        response = await c.post("/upload/1", content=chunks())
    assert response.status_code == 413
    # The app never sees bytes past the limit.
    assert all(len(body) <= 10 for body in app.bodies)


@pytest.mark.parametrize(("path", "body"), [("/upload/1", b"x" * 10), ("/other", b"x" * 100)])
async def test_small_or_unmatched_requests_pass_through(path, body):
    app = Recorder()
    async with client(app) as c:
        response = await c.post(path, content=body)
    assert response.status_code == 200 and app.bodies == [body]
