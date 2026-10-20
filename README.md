# ⚡ Codex CLI ↔ llama.cpp Namespace Proxy

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0%20(Pure%20Stdlib)-brightgreen.svg)](#requirements)
[![Tests Passing](https://img.shields.io/badge/tests-41%2F41%20passing-success.svg)](#-running-tests)

A lightweight, high-performance, **zero-dependency** middleware proxy enabling **OpenAI Codex CLI** to seamlessly execute functions and tools using local models served by **`llama.cpp`** (`llama-server`).

---

## 🧐 The Problem

When running agentic tasks with `wire_api = "responses"`, **Codex CLI** wraps file inspection and shell execution tools inside `type: "namespace"` container definitions:

```json
{
  "type": "namespace",
  "name": "codex",
  "tools": [
    { "type": "function", "name": "shell", ... },
    { "type": "function", "name": "read_file", ... }
  ]
}
```

However, the Responses API shim in **`llama.cpp`** only parses flat `type: "function"` objects. Upon encountering nested namespaces, it drops them silently:

```text
W srv server_chat_: unsupported Responses tool type 'namespace' skipped
W srv server_chat_: unsupported Responses tool type 'web_search' skipped
```

👉 **Result:** The local model receives zero tools, causing commands to fail, hallucinate, or hang during execution.

---

## 💡 The Solution

This proxy bridges the protocol gap bidirectionally in real-time:

```
┌─────────────┐                      ┌────────────────────────┐                      ┌─────────────┐
│  Codex CLI  │ ──(POST /responses)─►│  codex-llamacpp-proxy  │ ──(POST /responses)─►│  llama.cpp  │
│             │                      │       (:8081)          │                      │   (:8080)   │
└─────────────┘                      └────────────────────────┘                      └─────────────┘
                                                  │
                                [Request Transformation]
                                 Unpacks `namespace` tools
                                 into flat functions:
                                 `codex__shell`
                                                  │
                                [Response Transformation]
                                 Rewrites model calls back to
                                 `{"name": "shell", "namespace": "codex"}`
```

- ✅ **Full Tool Compatibility**: Model receives and runs shell & file commands properly.
- ✅ **Zero Third-Party Dependencies**: Pure Python standard library (`urllib` + `http.server`).
- ✅ **Cross-Platform**: Tested on Windows (PowerShell / Command Prompt) and Linux / macOS (Bash / Zsh).

---

## 🚀 Quick Start Guide

### Step 1: Clone the Repository

<details open>
<summary><b>🪟 Windows (PowerShell)</b></summary>

```powershell
git clone https://github.com/arefabacworkshop/codex-llamacpp-proxy.git
cd codex-llamacpp-proxy
```
</details>

<details open>
<summary><b>🐧 Linux / 🍎 macOS (Terminal)</b></summary>

```bash
git clone https://github.com/arefabacworkshop/codex-llamacpp-proxy.git
cd codex-llamacpp-proxy
chmod +x start.sh
```
</details>

---

### Step 2: Start the Proxy

Keep this terminal window running in the background.

<details open>
<summary><b>🪟 Windows</b></summary>

```powershell
.\start.bat
```
*(Or manually: `python proxy.py`)*
</details>

<details open>
<summary><b>🐧 Linux / 🍎 macOS</b></summary>

```bash
./start.sh
```
*(Or manually: `python3 proxy.py`)*
</details>

Output will confirm the connection:
```text
  [proxy] Codex CLI <-> llama.cpp namespace proxy
  [proxy] Listening  : http://127.0.0.1:8081/v1/
  [proxy] Upstream   : http://127.0.0.1:8080/v1/
  [proxy] Press Ctrl+C to stop
```

---

### Step 3: Configure Codex CLI

Codex configuration files are located in your home directory under `.codex/`.

#### 1. Define Model Catalog (`model-llamacpp.json`)

<details open>
<summary><b>🪟 Windows path: <code>%USERPROFILE%\.codex\model-llamacpp.json</code></b></summary>

```json
{
  "models": [
    {
      "slug": "your-model-name.gguf",
      "display_name": "Local LLM (llama.cpp)",
      "context_window": 32768,
      "default_reasoning_level": "medium",
      "default_verbosity": "low",
      "shell_type": "default",
      "supported_in_api": true,
      "input_modalities": ["text"],
      "supported_reasoning_levels": [
        { "effort": "low", "description": "Fast thinking" },
        { "effort": "medium", "description": "Balanced" },
        { "effort": "high", "description": "Deep thinking" }
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
</details>

<details open>
<summary><b>🐧 Linux / 🍎 macOS path: <code>~/.codex/model-llamacpp.json</code></b></summary>

```json
{
  "models": [
    {
      "slug": "your-model-name.gguf",
      "display_name": "Local LLM (llama.cpp)",
      "context_window": 32768,
      "default_reasoning_level": "medium",
      "default_verbosity": "low",
      "shell_type": "default",
      "supported_in_api": true,
      "input_modalities": ["text"],
      "supported_reasoning_levels": [
        { "effort": "low", "description": "Fast thinking" },
        { "effort": "medium", "description": "Balanced" },
        { "effort": "high", "description": "Deep thinking" }
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
</details>

> 💡 **Tip:** Change `"your-model-name.gguf"` to match the alias/filename loaded in your llama.cpp server.

---

#### 2. Update Codex Settings (`config.toml`)

Open your `config.toml` (`~/.codex/config.toml` or `C:\Users\<username>\.codex\config.toml`).

> ⚠️ **Important:** In TOML, root-level keys like `model`, `model_provider`, and `model_catalog_json` **must be placed at the very top**, before any `[...]` section tables!

```toml
# Top-level settings (must precede any [sections])
model              = "your-model-name.gguf"
model_provider     = "llamacpp"
model_catalog_json = "C:\\Users\\yourname\\.codex\\model-llamacpp.json" # on Linux: "/home/yourname/.codex/model-llamacpp.json"

[tui]
screen_reader_detection_done = true

[windows]
sandbox = "unelevated"

[model_providers.llamacpp]
name     = "llamacpp"
base_url = "http://127.0.0.1:8081/v1/"   # Point to the proxy port, NOT 8080!
wire_api = "responses"
```

---

### Step 4: Restart Daemon & Launch Codex

In a new terminal window:

<details open>
<summary><b>🪟 Windows (PowerShell)</b></summary>

```powershell
# Restart the daemon to flush cached configs
codex app-server daemon restart

# Start Codex CLI
codex
```
</details>

<details open>
<summary><b>🐧 Linux / 🍎 macOS (Bash / Zsh)</b></summary>

```bash
# Restart the daemon to flush cached configs
codex app-server daemon restart

# Start Codex CLI
codex
```
</details>

---

## ⚙️ Advanced CLI Options

You can customize listening addresses and ports:

```bash
python proxy.py [OPTIONS]
```

| Flag | Default | Description |
|---|---|---|
| `--proxy-port` | `8081` | Port where this proxy listens for Codex requests |
| `--upstream-port` | `8080` | Port of your running `llama.cpp` instance |
| `--upstream-host` | `127.0.0.1` | Host of your `llama.cpp` instance |

#### Custom Example:
```bash
python proxy.py --proxy-port 9000 --upstream-host 192.168.1.50 --upstream-port 8080
```

---

## 🧪 Running Tests

A comprehensive test suite is included covering tool flattening transformations, JSON-RPC schema mutations, and live end-to-end server integration.

```bash
# 1. Run offline unit tests only (instant)
python test_proxy.py --unit

# 2. Run automated test suite with auto-start/stop proxy
python test_proxy.py --auto-start
```

Sample output:
```text
-- Unit: flatten_tools --
  OK  plain function passes through
  OK  namespace flattened to 1 function
  OK  prefixed name is codex__shell
  OK  web_search dropped
  ...

-- Integration: live proxy --
  OK  I-1 proxy reachable
  OK  I-2 /health passthrough
  OK  I-3 simple prompt status 200
  OK  I-4 namespace tools -> no upstream error
  ...
───────────────────────────────────────────────────────
  Results: 41/41 passed  - ALL PASS
```

---

## 📂 Project Structure

```text
codex-llamacpp-proxy/
├── proxy.py           # Core HTTP proxy server (Zero external dependencies)
├── test_proxy.py      # Comprehensive test runner (Unit + Integration)
├── start.bat          # 1-Click launcher script for Windows
├── start.sh           # 1-Click executable launcher script for Linux/macOS
├── README.md          # Documentation & setup guide
└── .gitignore         # Standard Python ignore rules
```

---

## 🤝 Contributing

Contributions, bug reports, and PRs are welcome! If you notice edge cases with newer versions of Codex CLI or `llama.cpp`, feel free to open an issue or pull request.

## 📄 License

Licensed under the [MIT License](LICENSE).
