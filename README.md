# Confluence Local MCP Server

A local Model Context Protocol server for Confluence Cloud, rewritten in Python.
It connects directly to the Confluence REST API using Basic Auth and reads secrets
from a local `.env` file via `python-dotenv`.

## What the server exposes

The Python server preserves the original 5 Confluence tools:

| Tool | Purpose |
|---|---|
| `search_confluence_pages` | Search pages by keyword, topic, or business term |
| `read_confluence_page` | Read a page by numeric page ID |
| `get_page_children` | List direct child pages of a parent page |
| `create_confluence_page` | Create a page in the allowed folder |
| `update_confluence_page` | Update an existing page in the allowed folder |

## How it works

```
Claude Code / MCP client
        ↓
`server.py` (stdio JSON-RPC / MCP)
        ↓
Confluence Cloud REST API
        ↓
Pages, folders, and content
```

## Environment variables

The server loads `.env` from the project root at startup and overrides any
previously exported shell values.

Required values:

- `ATLASSIAN_EMAIL`
- `ATLASSIAN_API_TOKEN`
- `ALLOWED_FOLDER_ID`
- `CONFLUENCE_BASE_URL`

## Setup

```bash
cd /Users/vladashamshukaeva/confluence-local-mcp
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with your real values.

## Run the server

```bash
python server.py
```

The server speaks MCP over stdio, so it is usually started by your MCP client
instead of being run manually.

## Claude Code configuration

Use this JSON in your Claude Code MCP config:

```json
{
  "mcpServers": {
    "confluence": {
      "command": "python3",
      "args": ["/Users/vladashamshukaeva/confluence-local-mcp/server.py"],
      "env": {
        "ATLASSIAN_EMAIL": "${ATLASSIAN_EMAIL}",
        "ATLASSIAN_API_TOKEN": "${ATLASSIAN_API_TOKEN}",
        "ALLOWED_FOLDER_ID": "${ALLOWED_FOLDER_ID}"
      }
    }
  }
}
```

`server.py` still reads the local `.env` file, so your real credentials stay out
of the MCP config.

If your environment exposes `python` as the interpreter alias, you can swap
`python3` back to `python` in this JSON.

## Repo layout

```text
confluence-local-mcp/
  server.py
  requirements.txt
  .env.example
  .gitignore
  README.md
  diagnose.py
```

## Security notes

- `.env` is ignored by Git.
- Tokens are never hardcoded in source.
- The write tools are limited to `ALLOWED_FOLDER_ID`.
- Page bodies are capped at 1 MB.

