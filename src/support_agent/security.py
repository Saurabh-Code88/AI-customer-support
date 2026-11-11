"""Small ASGI middleware for limiting inbound request body size."""
import json


class RequestSizeLimitMiddleware:
    def __init__(self, app, max_bytes: int, *, secure_hsts: bool = False):
        self.app = app
        self.max_bytes = max_bytes
        self.secure_hsts = secure_hsts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                ])
                if self.secure_hsts:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message = {**message, "headers": headers}
            await send(message)

        if scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, secure_send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    await self._too_large(scope, secure_send)
                    return
            except ValueError:
                pass

        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > self.max_bytes:
                    await self._too_large(scope, secure_send)
                    return
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay_receive, secure_send)

    async def _too_large(self, scope, send):
        body = json.dumps({"error": {
            "code": "request_too_large", "message": "Request body is too large.",
        }}).encode()
        await send({"type": "http.response.start", "status": 413, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        ]})
        await send({"type": "http.response.body", "body": body})
