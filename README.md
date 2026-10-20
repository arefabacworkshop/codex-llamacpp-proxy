# codex-llamacpp-proxy

A lightweight, zero-dependency proxy that makes **Codex CLI** work with a local **llama.cpp** server.

## The Problem

Codex CLI sends its shell/file tools wrapped in `type: "namespace"` containers via the OpenAI Responses API (`/v1/responses`). The llama.cpp server only understands flat `type: "function"` tools and **silently drops all namespace tools** — so the model never sees Codex's tools and can't execute any commands.

```
Codex CLI  →  POST /v1/responses  →  llama.cpp
                 tools: [
                   { type: "namespace",   ← DROPPED by llama.cpp
                     name: "codex",
                     tools: [shell, read_file, ...] }
                 ]
```

## The Fix

This proxy sits between Codex and llama.cpp and handles the translation transparently:

```
Codex CLI → proxy (:8081) → llama.cpp (:8080)

Request:   namespace tools   →  flattened functions  (codex__shell, codex__read_file …)
Response:  prefixed tool call ←  namespace tool call  (name: shell, namespace: codex)
```

All other routes (`/v1/models`, `/health`, etc.) are passed through unchanged.

## Requirements

- Python 3.8+ (no third-party packages — pure stdlib)
- llama.cpp server running on `localhost:8080` with `--api-server` (or `llama-server`)
- Codex CLI installed

## Quick Start

**1. Start the proxy** (keep this terminal open):

```bash
# Windows
start.bat

# macOS / Linux
python proxy.py
```

**2. Point Codex at the proxy** — edit `~/.codex/config.toml`:

```toml
model              = "<your-model-name>"
model_provider     = "llamacpp"
model_catalog_json = "/path/to/your/model-catalog.json"

[model_providers.llamacpp]
name     = "llamacpp"
base_url = "http://127.0.0.1:8081/v1/"   # <-- proxy port, not 8080
wire_api = "responses"
```

**3. Launch Codex** in another terminal:

```bash
codex
```

## Usage

```
python proxy.py [--proxy-port PORT] [--upstream-port PORT] [--upstream-host HOST]

Options:
  --proxy-port      Port this proxy listens on       (default: 8081)
  --upstream-port   llama.cpp server port            (default: 8080)
  --upstream-host   llama.cpp server host            (default: 127.0.0.1)
```

**Examples:**

```bash
# Default (proxy :8081 → llama.cpp :8080)
python proxy.py

# Custom ports
python proxy.py --proxy-port 9000 --upstream-port 8080

# Remote llama.cpp instance
python proxy.py --upstream-host 192.168.1.10 --upstream-port 8080
```

## How It Works

### Request (Codex → llama.cpp)

```
Input tools from Codex:
  [
    { "type": "namespace", "name": "codex", "tools": [
        { "type": "function", "name": "shell", ... },
        { "type": "function", "name": "read_file", ... }
    ]},
    { "type": "web_search" }   ← also dropped gracefully
  ]

After flattening (what llama.cpp receives):
  [
    { "type": "function", "name": "codex__shell", ... },
    { "type": "function", "name": "codex__read_file", ... }
  ]
```

### Response (llama.cpp → Codex)

```
From llama.cpp:
  { "type": "function_call", "name": "codex__shell", "arguments": "..." }

After restoration (what Codex receives):
  { "type": "function_call", "name": "shell", "namespace": "codex", "arguments": "..." }
```

## Running the Tests

```bash
# Unit tests only (no network needed)
python test_proxy.py --unit

# Full suite (proxy must already be running on :8081)
python test_proxy.py

# Full suite — proxy is auto-started and stopped
python test_proxy.py --auto-start
```

Expected output:
```
-- Unit: flatten_tools --
  OK  plain function passes through
  OK  namespace flattened to 1 function
  ...

-- Integration: live proxy --
  OK  I-1 proxy reachable
  OK  I-4 namespace tools -> no upstream error
  ...
  Results: 41/41 passed  - ALL PASS
```

## Full Codex + llama.cpp Setup

If you're setting up from scratch, here's the complete `~/.codex/config.toml`:

```toml
# Top-level keys must come before any [section] headers
model              = "your-model-name.gguf"
model_provider     = "llamacpp"
model_catalog_json = "C:\\Users\\you\\.codex\\model-llamacpp.json"

[tui]
screen_reader_detection_done = true

[windows]
sandbox = "unelevated"

[model_providers.llamacpp]
name     = "llamacpp"
base_url = "http://127.0.0.1:8081/v1/"
wire_api = "responses"
```

And `~/.codex/model-llamacpp.json`:

```json
{
  "models": [
    {
      "slug": "your-model-name.gguf",
      "display_name": "My Local Model (llamacpp)",
      "context_window": 32768,
      "default_reasoning_level": "medium",
      "default_verbosity": "low",
      "shell_type": "default",
      "supported_in_api": true,
      "input_modalities": ["text"],
      "supported_reasoning_levels": [
        { "effort": "low",    "description": "Fast responses" },
        { "effort": "medium", "description": "Balanced" },
        { "effort": "high",   "description": "Deep reasoning" }
      ],
      "supports_parallel_tool_calls": false,
      "supports_reasoning_summaries": false,
      "visibility": "list",
      "priority": 0,
      "truncation_policy": { "limit": 10000, "mode": "bytes" }
    }
  ]
}
```

## Why Not Fix This in llama.cpp?

Active PRs exist upstream ([#25193](https://github.com/ggml-org/llama.cpp/issues/25193), [#24295](https://github.com/ggml-org/llama.cpp/issues/24295)) to natively support `namespace` tool types in `llama-server`. Once merged, this proxy won't be needed. Until then, this is the cleanest workaround.

## File Structure

```
codex-llamacpp-proxy/
├── proxy.py        # The proxy (zero dependencies)
├── test_proxy.py   # Full test suite (unit + integration)
├── start.bat       # Windows one-click launcher
└── README.md
```

## License

MIT
