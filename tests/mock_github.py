from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

type JsonBody = dict[str, Any] | list[Any]
type RouteMap = Mapping[tuple[str, str], tuple[int, JsonBody]]


class MockGitHubServer:
    def __init__(self, routes: RouteMap) -> None:
        self.routes = routes
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = cast(tuple[str, int], self.httpd.server_address)
        return f"http://{host}:{port}"

    def __enter__(self) -> MockGitHubServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=5)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._respond("GET")

            def do_POST(self) -> None:
                self._respond("POST")

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _respond(self, method: str) -> None:
                status, body = routes.get((method, self.path), (404, {"message": "not found"}))
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("x-github-request-id", "TEST123")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        return Handler
