# Confluence Local MCP Server

A local Model Context Protocol server for Confluence Cloud, written in Python.
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

## First-time setup

1. **Install prerequisites**
   - Git
   - Python 3.10+ (3.11 or newer is fine)
   - A Confluence account with access to the target space/folder

2. **Clone the repo**
   ```bash
   git clone <YOUR_GITHUB_REPO_URL> confluence-mcp
   cd confluence-mcp
   ```

3. **Create an Atlassian API token**
   - Open https://id.atlassian.com/manage-profile/security/api-tokens
   - Click **Create API token**
   - Copy the token and store it securely
   - Use your Atlassian email address as the username

4. **Create your local `.env` file**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and fill in:
   - `ATLASSIAN_EMAIL`
   - `ATLASSIAN_API_TOKEN`
   - `CONFLUENCE_BASE_URL`
   - `ALLOWED_FOLDER_ID`

5. **Install Python dependencies**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   python3 -m pip install -r requirements.txt
   ```

6. **Check that authentication works**
   ```bash
   python3 diagnose.py
   ```
   If Basic Auth fails, the token or email is wrong. If Bearer auth works, the
   token is valid for Atlassian’s API gateway.

## Run the server

```bash
python3 server.py
```

The server speaks MCP over stdio, so it is usually started by your MCP client
instead of being run manually.

## Claude Code configuration

Use this JSON in your Claude Code MCP config:

```json
{
  "servers": {
    "confluence": {
      "command": "/Library/Developer/CommandLineTools/usr/bin/python3",
      "args": ["-u", "/path/to/confluence-mcp/server.py"]
    }
  }
}
```

`server.py` reads the local `.env` file, so your real credentials stay out of
the MCP config.

If your environment exposes `python` as the interpreter alias, you can swap
`python3` back to `python` in this JSON.

## Repo layout

```text
confluence-mcp/
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
