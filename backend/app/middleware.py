"""ASGI request-size guard that runs before multipart parsing spools to disk."""

import json
import re

TOO_LARGE = json.dumps({"error": {"code": "PAYLOAD_TOO_LARGE", "message": "The request body is too large."}}).encode()


class RequestBodyLimit:
    """Rejects matching POST bodies above `max_bytes` with 413.

    A declared Content-Length above the limit is refused before the app reads
    anything. Chunked or understated bodies are counted while streaming; once the
    limit is crossed the app sees end-of-body and whatever response it produces is
    replaced with the same 413, so nothing beyond the limit is buffered or spooled.
    """

    def __init__(self, app, *, max_bytes: int, path_pattern: str):
        self.app = app
        self.max_bytes = max_bytes
        self.pattern = re.compile(path_pattern)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not self.pattern.fullmatch(scope["path"]):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
            except ValueError:
                too_large = True
            if too_large:
                await self._reject(send)
                return

        received = 0
        exceeded = False

        async def limited_receive():
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        replaced = False

        async def guarded_send(message):
            nonlocal replaced
            if exceeded:
                if not replaced:
                    replaced = True
                    await self._reject(send)
                return
            await send(message)

        await self.app(scope, limited_receive, guarded_send)

    @staticmethod
    async def _reject(send):
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(TOO_LARGE)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": TOO_LARGE})
