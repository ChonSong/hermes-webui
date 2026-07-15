# Open in Workspace — Hermes WebUI Extension

Adds an **"Open in workspace"** button to `read_file` tool cards. When you click it, the file opens in the workspace file-tree panel — the same behavior as clicking an artifact link.

## How it works

- Watches for new tool-card-row elements with `data-tool-kind="read"`
- Extracts the file path from `row._tcData.args` (or the rendered args section)
- Injects a Lucide folder-open button inside `.tool-card-detail`
- Uses `MutationObserver` so it works on dynamically-rendered cards

## Install

From **Settings → Extensions → Gallery**, find **Open in Workspace** and click Install.

Or configure manually:

```bash
# Clone or copy the extension files, then:
export HERMES_WEBUI_EXTENSION_DIR=/path/to/open-in-workspace-ext
export HERMES_WEBUI_EXTENSION_MANIFEST=manifest.json
./start.sh
```
