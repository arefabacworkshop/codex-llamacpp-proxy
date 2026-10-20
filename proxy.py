#!/usr/bin/env python3
"""
codex-llamacpp-proxy
====================
Transparent proxy between Codex CLI and llama.cpp server.

Problem
-------
Codex CLI sends its shell/file tools wrapped in `type: "namespace"` containers
via the OpenAI Responses API (/v1/responses). llama.cpp's Responses API shim
only understands flat `type: "function"` tools and silently drops namespace
ones, so the model never receives any of Codex's tools and can't execute
commands.

Fix
---
- Intercepts  POST /v1/responses  from Codex
- Flattens    namespace → flat functions   (prefixed as  ns__toolname)
- Forwards to llama.cpp
- Rewrites    prefixed tool calls → namespace format   in the response
- All other routes are transparently proxied unchanged

Usage
-----
  python proxy.py                          # default: :8081 → :8080
  python proxy.py --proxy-port 8081 --upstream-port 8080

Then set  base_url = "http://127.0.0.1:8081/v1/"  in your Codex provider config.

Requirements
------------
Python 3.8+, zero third-party dependencies (pure stdlib).
"""

import json
import sys
import argparse
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── defaults ──────────────────────────────────────────────────────────────────
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8080
PROXY_PORT    = 8081
# ─────────────────────────────────────────────────────────────────────────────


def flatten_tools(tools: list) -> tuple[list, dict]:
    """
    Flatten namespace tool containers into standard function tools.

    Converts:
        [{"type":"namespace","name":"codex","tools":[
            {"type":"function","name":"shell", ...}
        ]}]
    Into:
        [{"type":"function","name":"codex__shell", ...}]

    Returns:
        flat_tools  – list of flat function tool dicts
        ns_map      – {prefixed_name: (namespace_name, original_name)}
                      used later to rewrite tool calls in the response
    """
    flat   = []
    ns_map = {}

    for tool in tools:
        kind = tool.get("type", "")

        if kind == "function":
            flat.append(tool)

        elif kind == "namespace":
            ns_name = tool.get("name", "ns")
            for sub in tool.get("tools", []):
                if sub.get("type") == "function":
                    orig     = sub["name"]
                    prefixed = f"{ns_name}__{orig}"
                    ns_map[prefixed] = (ns_name, orig)
                    flat.append({**sub, "name": prefixed})

        # web_search, computer_use, etc. — silently dropped
        # (llama.cpp would drop them too, but now without the warning spam)

    return flat, ns_map


def restore_output(output: list, ns_map: dict) -> list:
    """
    Rewrite prefixed function_call names in the model output back to the
    namespace format Codex expects.

    Before:  {"type":"function_call", "name":"codex__shell",  "arguments":"..."}
    After:   {"type":"function_call", "name":"shell", "namespace":"codex", "arguments":"..."}
    """
    result = []
    for item in output:
        if item.get("type") == "function_call":
            name = item.get("name", "")
            if name in ns_map:
                ns_name, orig = ns_map[name]
                item = {**item, "name": orig, "namespace": ns_name}
        result.append(item)
    return result


class ProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  [proxy] {fmt % args}", file=sys.stderr, flush=True)

    # ── routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        self._passthrough()

    def do_POST(self):
        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b""
        if self.path.rstrip("/") == "/v1/responses":
            self._handle_responses(raw_body)
        else:
            self._passthrough(raw_body)

    def do_DELETE(self):
        self._passthrough()

    def do_PUT(self):
        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b""
        self._passthrough(raw_body)

    # ── /v1/responses interception ────────────────────────────────────────────

    def _handle_responses(self, raw_body: bytes):
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON in request body")
            return

        # llama.cpp's Responses shim has issues with streaming; force it off
        # so we always get a clean JSON object back that we can patch.
        body["stream"] = False

        # Flatten namespace tools → flat functions
        flat_tools, ns_map = flatten_tools(body.get("tools", []))
        body["tools"] = flat_tools

        if ns_map:
            print(
                f"  [proxy] flattened {len(ns_map)} tool(s): {list(ns_map.values())}",
                file=sys.stderr, flush=True,
            )

        # Forward to llama.cpp
        upstream_url = f"http://{UPSTREAM_HOST}:{UPSTREAM_PORT}/v1/responses"
        req = urllib.request.Request(
            upstream_url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw_resp = resp.read()
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            return
        except Exception as exc:
            self.send_error(502, f"Upstream error: {exc}")
            return

        # Patch response: rewrite prefixed tool calls back to namespace format
        try:
            resp_data = json.loads(raw_resp)
            if "output" in resp_data and ns_map:
                resp_data["output"] = restore_output(resp_data["output"], ns_map)
            out_body = json.dumps(resp_data).encode()
        except Exception:
            out_body = raw_resp  # pass through unmodified on parse failure

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out_body)))
        self.end_headers()
        self.wfile.write(out_body)

    # ── generic passthrough ───────────────────────────────────────────────────

    def _passthrough(self, body: bytes = b""):
        upstream_url = f"http://{UPSTREAM_HOST}:{UPSTREAM_PORT}{self.path}"
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length")
        }
        if body:
            headers["Content-Length"] = str(len(body))

        req = urllib.request.Request(
            upstream_url,
            data=body or None,
            headers=headers,
            method=self.command,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as exc:
            self.send_error(502, str(exc))


def main():
    global UPSTREAM_HOST, UPSTREAM_PORT, PROXY_PORT

    parser = argparse.ArgumentParser(
        description="Codex CLI ↔ llama.cpp namespace-flattening proxy"
    )
    parser.add_argument("--upstream-host", default=UPSTREAM_HOST,
                        help="llama.cpp host (default: 127.0.0.1)")
    parser.add_argument("--upstream-port", type=int, default=UPSTREAM_PORT,
                        help="llama.cpp port (default: 8080)")
    parser.add_argument("--proxy-port", type=int, default=PROXY_PORT,
                        help="Port this proxy listens on (default: 8081)")
    args = parser.parse_args()

    UPSTREAM_HOST = args.upstream_host
    UPSTREAM_PORT = args.upstream_port
    PROXY_PORT    = args.proxy_port

    print(f"  [proxy] Codex CLI <-> llama.cpp namespace proxy", file=sys.stderr)
    print(f"  [proxy] Listening  : http://127.0.0.1:{PROXY_PORT}/v1/", file=sys.stderr)
    print(f"  [proxy] Upstream   : http://{UPSTREAM_HOST}:{UPSTREAM_PORT}/v1/", file=sys.stderr)
    print(f"  [proxy] Press Ctrl+C to stop", file=sys.stderr, flush=True)

    server = HTTPServer(("127.0.0.1", PROXY_PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  [proxy] Stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
