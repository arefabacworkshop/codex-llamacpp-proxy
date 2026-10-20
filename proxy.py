#!/usr/bin/env python3
"""
codex-llamacpp-proxy
====================
Transparent proxy between Codex CLI and llama.cpp server.

Features:
- Native SSE Streaming (Server-Sent Events) & Non-streaming fallback
- Tool flattening (namespace -> flat functions with prefix)
- Live tool call restoration in both SSE stream events and non-streaming responses
- Normalizes mid-conversation system/developer messages so strict Jinja templates
  (e.g., Qwen 3.5/3.8) never crash with 'System message must be at the beginning'
- Transparent passthrough for all other endpoints
"""

import json
import sys
import argparse
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# ── defaults ──────────────────────────────────────────────────────────────────
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8080
PROXY_PORT    = 8081
# ─────────────────────────────────────────────────────────────────────────────


def flatten_tools(tools: list) -> tuple[list, dict]:
    """
    Flatten namespace tool containers into standard function tools.
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

    return flat, ns_map


def normalize_input_messages(body: dict) -> tuple[dict, bool]:
    """
    Prevents Jinja template exceptions in models like Qwen 3.5/3.8
    ('System message must be at the beginning.').

    In multi-turn chat history (resuming or continuing chats), Codex CLI appends
    updated environment instructions or system reminders inside the 'input' array.
    When llama.cpp converts these to internal chat completion turns, having a
    system/developer role anywhere other than the very first turn trips the strict
    Jinja assertion.

    Solution:
    Any system/developer turn after the first message is safely downgraded to a
    user instruction turn ('user' role with '[System Notice]' prefix).
    """
    raw_input = body.get("input")
    if not isinstance(raw_input, list):
        return body, False

    modified = False
    new_input = []

    # Check if a top-level 'instructions' exists (llama.cpp maps it to index 0 system)
    has_top_instructions = bool(body.get("instructions"))
    has_seen_first_message = has_top_instructions

    for idx, item in enumerate(raw_input):
        if not isinstance(item, dict):
            new_input.append(item)
            continue

        role = item.get("role")
        if role in ("system", "developer"):
            # If not the very first message in the entire conversation
            if has_seen_first_message:
                item_copy = dict(item)
                item_copy["role"] = "user"
                content = item_copy.get("content")
                if isinstance(content, str):
                    item_copy["content"] = f"[System Notice]: {content}"
                elif isinstance(content, list):
                    # Part-based content list
                    new_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                            p_copy = dict(part)
                            p_copy["text"] = f"[System Notice]: {p_copy.get('text', '')}"
                            new_parts.append(p_copy)
                        else:
                            new_parts.append(part)
                    item_copy["content"] = new_parts
                new_input.append(item_copy)
                modified = True
                continue

        has_seen_first_message = True
        new_input.append(item)

    if modified:
        body["input"] = new_input

    return body, modified


def patch_json_item(item: dict, ns_map: dict) -> dict:
    """Rewrite prefixed tool call names back to namespace format."""
    if item.get("type") == "function_call":
        name = item.get("name", "")
        if name in ns_map:
            ns_name, orig = ns_map[name]
            item = {**item, "name": orig, "namespace": ns_name}
    return item


def restore_output(output: list, ns_map: dict) -> list:
    """Rewrite prefixed function_call names in non-streaming responses."""
    result = []
    for item in output:
        result.append(patch_json_item(item, ns_map))
    return result


def patch_sse_data(data_str: str, ns_map: dict) -> str:
    """
    Patch Server-Sent Event JSON data if it represents a function call or contains output items.
    """
    if not ns_map or not data_str:
        return data_str

    try:
        data = json.loads(data_str)
    except Exception:
        return data_str

    evt_type = data.get("type", "")

    # 1. response.output_item.added / response.output_item.done
    if evt_type in ("response.output_item.added", "response.output_item.done"):
        if "item" in data and isinstance(data["item"], dict):
            data["item"] = patch_json_item(data["item"], ns_map)
            return json.dumps(data)

    # 2. response.completed contains full output list
    elif evt_type == "response.completed":
        if "response" in data and "output" in data["response"]:
            data["response"]["output"] = restore_output(data["response"]["output"], ns_map)
            return json.dumps(data)

    return data_str


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class ProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        msg = fmt % args
        if "responses" in msg or "40" in msg or "50" in msg:
            print(f"  [proxy] {msg}", file=sys.stderr, flush=True)

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

        is_streaming = body.get("stream", False)

        # 1. Normalize input messages to prevent mid-conversation system prompt crashes
        body, normalized = normalize_input_messages(body)
        if normalized:
            print("  [proxy] Normalized mid-conversation system message -> user notice", file=sys.stderr, flush=True)

        # 2. Flatten namespace tools → flat functions
        flat_tools, ns_map = flatten_tools(body.get("tools", []))
        body["tools"] = flat_tools

        if ns_map:
            print(
                f"  [proxy] flattened {len(ns_map)} tool(s): {list(ns_map.values())} (streaming={is_streaming})",
                file=sys.stderr, flush=True,
            )

        upstream_url = f"http://{UPSTREAM_HOST}:{UPSTREAM_PORT}/v1/responses"
        req = urllib.request.Request(
            upstream_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream" if is_streaming else "application/json"},
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=600)
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

        # ── Streaming handling (SSE) ──────────────────────────────────────────
        if is_streaming:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            try:
                while True:
                    line = resp.readline()
                    if not line:
                        break

                    decoded = line.decode("utf-8", errors="replace")
                    if decoded.startswith("data: "):
                        data_part = decoded[6:].strip()
                        patched_data = patch_sse_data(data_part, ns_map)
                        out_line = f"data: {patched_data}\n\n".encode("utf-8")
                        self.wfile.write(out_line)
                    else:
                        self.wfile.write(line)
                    self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
            finally:
                resp.close()
            return

        # ── Non-streaming handling ────────────────────────────────────────────
        try:
            raw_resp = resp.read()
            resp_data = json.loads(raw_resp)
            if "output" in resp_data and ns_map:
                resp_data["output"] = restore_output(resp_data["output"], ns_map)
            out_body = json.dumps(resp_data).encode("utf-8")
        except Exception:
            out_body = raw_resp
        finally:
            resp.close()

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
            with urllib.request.urlopen(req, timeout=120) as resp:
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
        description="Codex CLI <-> llama.cpp streaming & namespace-flattening proxy"
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

    print(f"  [proxy] Codex CLI <-> llama.cpp namespace & streaming proxy", file=sys.stderr)
    print(f"  [proxy] Listening  : http://127.0.0.1:{PROXY_PORT}/v1/", file=sys.stderr)
    print(f"  [proxy] Upstream   : http://{UPSTREAM_HOST}:{UPSTREAM_PORT}/v1/", file=sys.stderr)
    print(f"  [proxy] Press Ctrl+C to stop", file=sys.stderr, flush=True)

    server = ThreadedHTTPServer(("127.0.0.1", PROXY_PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  [proxy] Stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
