# openclaw-token-tracker

Sanitized OpenClaw token usage dashboard.

## Publish/update locally

```bash
python3 scripts/export_usage.py
```

Preview:

```bash
python3 -m http.server 8080 -d site
```

## Privacy

Published data excludes message text, chat IDs, user IDs, prompts, tool arguments, and secrets.

## GitHub Pages

This repo publishes the `site/` directory via GitHub Pages.
