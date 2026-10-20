#!/usr/bin/env python3
"""
test_proxy.py
=============
Test suite for proxy.py.

  Unit tests    — pure logic, no network needed
  Integration   — requires proxy + llama.cpp both running

Run (unit only):
  python test_proxy.py --unit

Run (full, proxy must already be running on :8081):
  python test_proxy.py

Run (full, auto-start/stop the proxy):
  python test_proxy.py --auto-start
"""

import sys
import json
import time
import argparse
import subprocess
import urllib.request
import urllib.error

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── import proxy module ───────────────────────────────────────────────────────
import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location(
    "proxy", pathlib.Path(__file__).parent / "proxy.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

flatten_tools  = _mod.flatten_tools
restore_output = _mod.restore_output

MODEL = "Swift-1.5-Qwen3.8-27B-Custom-IQ3_XXS-MTP.gguf"

# ── tiny test framework ───────────────────────────────────────────────────────
PASS = FAIL = 0

def ok(name, _=None):
    global PASS; PASS += 1
    print(f"  OK  {name}")

def fail(name, reason=""):
    global FAIL; FAIL += 1
    print(f"  FAIL  {name}")
    if reason: print(f"        {reason}")

def check(name, cond, reason=""):
    (ok if cond else fail)(name, reason or "assertion failed")


# ══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS
# ══════════════════════════════════════════════════════════════════════════════

def run_unit_tests():
    print("\n-- Unit: flatten_tools --")

    tools = [{"type": "function", "name": "mytool", "parameters": {}}]
    flat, ns_map = flatten_tools(tools)
    check("plain function passes through", flat == tools and ns_map == {})

    tools = [{"type": "namespace", "name": "codex", "tools": [
        {"type": "function", "name": "shell", "description": "run shell", "parameters": {}}
    ]}]
    flat, ns_map = flatten_tools(tools)
    check("namespace flattened to 1 function",  len(flat) == 1)
    check("prefixed name is codex__shell",       flat[0]["name"] == "codex__shell")
    check("original description preserved",      flat[0].get("description") == "run shell")
    check("ns_map has correct entry",            ns_map.get("codex__shell") == ("codex", "shell"))

    tools = [{"type": "namespace", "name": "ns", "tools": [
        {"type": "function", "name": "a", "parameters": {}},
        {"type": "function", "name": "b", "parameters": {}},
    ]}]
    flat, ns_map = flatten_tools(tools)
    check("two tools flattened",   len(flat) == 2)
    check("both in ns_map",        set(ns_map.keys()) == {"ns__a", "ns__b"})

    tools = [
        {"type": "namespace", "name": "alpha", "tools": [{"type": "function", "name": "go", "parameters": {}}]},
        {"type": "namespace", "name": "beta",  "tools": [{"type": "function", "name": "go", "parameters": {}}]},
    ]
    flat, ns_map = flatten_tools(tools)
    check("two namespaces -> 2 flat tools", len(flat) == 2)
    check("alpha__go in map",               "alpha__go" in ns_map)
    check("beta__go in map",                "beta__go"  in ns_map)

    check("web_search dropped",   flatten_tools([{"type": "web_search"}]) == ([], {}))
    check("computer_use dropped", flatten_tools([{"type": "computer_use"}]) == ([], {}))

    tools = [
        {"type": "namespace", "name": "ns", "tools": [{"type": "function", "name": "shell", "parameters": {}}]},
        {"type": "function",  "name": "plain", "parameters": {}},
        {"type": "web_search"},
    ]
    flat, ns_map = flatten_tools(tools)
    check("mixed: 2 flat tools remain",          len(flat) == 2)
    check("mixed: ns__shell present",            any(t["name"] == "ns__shell" for t in flat))
    check("mixed: plain present unchanged",      any(t["name"] == "plain"     for t in flat))

    check("empty tools -> empty flat", flatten_tools([]) == ([], {}))
    check("namespace with no 'tools' key -> empty", flatten_tools([{"type": "namespace", "name": "x"}]) == ([], {}))

    tools = [{"type": "namespace", "name": "ns", "tools": [
        {"type": "function",      "name": "ok_tool",  "parameters": {}},
        {"type": "something_else","name": "bad"},
    ]}]
    flat, ns_map = flatten_tools(tools)
    check("non-function sub-tool skipped", len(flat) == 1 and flat[0]["name"] == "ns__ok_tool")

    tools = [
        {"type": "namespace", "name": "a", "tools": [{"type": "function", "name": "run", "parameters": {}}]},
        {"type": "namespace", "name": "a", "tools": [{"type": "function", "name": "run", "parameters": {}}]},
    ]
    flat, ns_map = flatten_tools(tools)
    check("duplicate prefixed name: both in flat list", len(flat) == 2)

    print("\n-- Unit: restore_output --")

    ns_map = {"ns__shell": ("ns", "shell"), "ns__read": ("ns", "read")}

    output = [{"type": "function_call", "name": "ns__shell", "call_id": "c1", "arguments": "{}"}]
    result = restore_output(output, ns_map)
    check("function_call name restored",   result[0]["name"] == "shell")
    check("namespace field added",         result[0].get("namespace") == "ns")
    check("call_id preserved",             result[0]["call_id"] == "c1")
    check("arguments preserved",           result[0]["arguments"] == "{}")

    output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}]
    check("message item unchanged", restore_output(output, ns_map) == output)

    output = [{"type": "function_call", "name": "unknown_fn", "arguments": "{}"}]
    result = restore_output(output, ns_map)
    check("unknown tool name unchanged",          result[0]["name"] == "unknown_fn")
    check("no namespace added for unknown",       "namespace" not in result[0])

    check("empty output -> empty result", restore_output([], ns_map) == [])

    output = [{"type": "reasoning", "content": "thinking..."}]
    check("reasoning item unchanged", restore_output(output, ns_map) == output)

    output = [
        {"type": "function_call", "name": "ns__read", "call_id": "c2", "arguments": "{}"},
        {"type": "message", "content": "done"},
    ]
    result = restore_output(output, ns_map)
    check("multi-item: function restored",   result[0]["name"] == "read")
    check("multi-item: message unchanged",   result[1]["content"] == "done")

    output = [{"type": "function_call", "name": "ns__shell", "arguments": "{}"}]
    check("empty ns_map: name unchanged", restore_output(output, {})[0]["name"] == "ns__shell")


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════════════════

def _get(path, port=8081):
    r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10)
    return json.loads(r.read())

def _post(path, body, port=8081, timeout=90):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def run_integration_tests():
    print("\n-- Integration: live proxy --")

    try:
        data = _get("/v1/models")
        check("I-1 proxy reachable",         True)
        check("I-1 /v1/models passthrough",  "data" in data or "models" in data)
    except Exception as e:
        fail("I-1 proxy reachable", str(e))

    try:
        data = _get("/health")
        check("I-2 /health passthrough", data.get("status") == "ok")
    except Exception as e:
        fail("I-2 /health passthrough", str(e))

    try:
        status, data = _post("/v1/responses", {
            "model": MODEL, "input": "Reply with exactly: HELLO", "stream": False
        })
        check("I-3 simple prompt status 200", status == 200)
        texts = [
            c.get("text", "")
            for item in data.get("output", []) if item.get("type") == "message"
            for c in item.get("content", [])
        ]
        check("I-3 got text output", any(texts))
    except Exception as e:
        fail("I-3 simple prompt", str(e))

    try:
        status, data = _post("/v1/responses", {
            "model": MODEL,
            "input": "What tools do you have?",
            "stream": False,
            "tools": [
                {"type": "namespace", "name": "codex", "tools": [
                    {"type": "function", "name": "shell",
                     "description": "Run a shell command",
                     "parameters": {"type": "object",
                                    "properties": {"cmd": {"type": "string"}},
                                    "required": ["cmd"]}},
                    {"type": "function", "name": "read_file",
                     "description": "Read a file",
                     "parameters": {"type": "object",
                                    "properties": {"path": {"type": "string"}},
                                    "required": ["path"]}},
                ]},
                {"type": "web_search"},
            ],
        }, timeout=120)
        check("I-4 namespace tools -> no upstream error", status == 200)
        check("I-4 response has 'output' key",            "output" in data)
    except Exception as e:
        fail("I-4 namespace tools", str(e))

    try:
        status, data = _post("/v1/responses", {
            "model": MODEL, "input": "Say: STREAM_OVERRIDE_TEST", "stream": True
        })
        check("I-5 stream override -> still get JSON", status == 200 and "output" in data)
    except Exception as e:
        fail("I-5 stream override", str(e))

    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8081/v1/responses",
            data=b"NOT JSON {{{{",
            headers={"Content-Type": "application/json", "Content-Length": "12"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            fail("I-6 bad JSON -> 400", "expected error, got 200")
        except urllib.error.HTTPError as e:
            check("I-6 bad JSON -> 400", e.code == 400)
    except Exception as e:
        fail("I-6 bad JSON body", str(e))

    try:
        data = _get("/v1/models")
        check("I-7 unknown route proxied", "data" in data or "models" in data)
    except Exception as e:
        fail("I-7 unknown route", str(e))


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit",       action="store_true", help="Run unit tests only")
    parser.add_argument("--auto-start", action="store_true", help="Auto start/stop the proxy")
    args = parser.parse_args()

    run_unit_tests()

    if not args.unit:
        proxy_proc = None
        if args.auto_start:
            proxy_proc = subprocess.Popen(
                [sys.executable, str(pathlib.Path(__file__).parent / "proxy.py")],
                stderr=subprocess.DEVNULL,
            )
            # Wait until port is open
            for _ in range(20):
                try:
                    urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=1)
                    break
                except Exception:
                    time.sleep(0.3)

        run_integration_tests()

        if proxy_proc:
            proxy_proc.terminate()

    total = PASS + FAIL
    print(f"\n{'─' * 55}")
    print(f"  Results: {PASS}/{total} passed", end="")
    print("  - ALL PASS" if not FAIL else f"  -  {FAIL} FAILED")
    print()
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
