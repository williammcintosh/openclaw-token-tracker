# V1 Plan

## Goal

Build a local exporter + static dashboard for OpenClaw OAuth usage tracking that can be safely published to GitHub Pages.

## Requirements

- Local-only data refresh; no model/API usage required for updates
- Read OpenClaw session/log files directly
- Store historical aggregates locally
- Publish only sanitised minimal data
- Rule-based categorisation
- Static dashboard that works on small phone screens

## Inspected OpenClaw data sources

### 1) `~/.openclaw/agents/main/sessions/sessions.json`
Useful current-session metadata / rollups.

Observed keys include:

- `sessionId`
- `sessionFile`
- `chatType`
- `channel`
- `subject`
- `displayName`
- `origin`
- `deliveryContext`
- `updatedAt`
- `modelProvider`
- `model`
- `contextTokens`
- `inputTokens`
- `outputTokens`
- `cacheRead`
- `cacheWrite`
- `totalTokens`
- `totalTokensFresh`
- `thinkingLevel`
- `spawnDepth`
- `spawnedBy`
- `systemPromptReport`

### 2) `~/.openclaw/agents/main/sessions/*.jsonl*`
Historical session event stream.

Observed event types:

- `session`
- `model_change`
- `thinking_level_change`
- `custom` (`model-snapshot`)
- `message`

Observed `message.role` values:

- `user`
- `assistant`
- `toolResult`

Observed assistant message fields used by exporter:

- `timestamp`
- `message.provider`
- `message.model`
- `message.stopReason`
- `message.usage.input`
- `message.usage.output`
- `message.usage.cacheRead`
- `message.usage.cacheWrite`
- `message.usage.totalTokens`
- `message.usage.cost.total`

### 3) `~/.openclaw/logs/config-audit.jsonl`
Inspected but not needed for v1 usage totals.

## Architecture

### Local exporter

1. Load current session metadata from `sessions.json`
2. Scan all session files (`*.jsonl*`, excluding locks)
3. Extract assistant usage events
4. Dedupe events across reset/deleted/current session files
5. Infer session kind + apply ordered category rules
6. Aggregate by:
   - day
   - category
   - model
   - provider
   - session kind
   - stop reason
7. Write:
   - local aggregate history (`data/private/history.json`)
   - publishable summary (`site/data/summary.json`)

### Static dashboard

- Fetches only `site/data/summary.json`
- No framework, no build step, no external JS
- Responsive cards, daily chart, breakdown bars, recent daily table

## Sanitisation rules

Public output excludes:

- raw session keys
- session IDs
- chat/group/user IDs
- raw message text
- tool arguments/results
- auth profile data / secrets

## V1 trade-offs

- Full scan each export run; no daemon/stateful watcher required
- Category rules are intentionally simple and configurable
- Uses assistant usage events for historical accuracy; `sessions.json` is metadata support, not the main history source

## Next improvements

- configurable date ranges
- compare periods (7d / 30d / all time)
- optional CSV export
- better project/category rules
- anomaly detection / spikes
