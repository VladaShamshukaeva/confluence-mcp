# Copilot instructions

- This repo is a local Confluence MCP server.
- Keep the tool surface to exactly 5 tools total: 3 read-action tools and 2 write-action tools.
- Write-action tools must only read and update files inside `ALLOWED_FOLDER_ID`.
- Never add tools, scripts, or fallback commands for unsupported requests.
- If there is no matching command, respond that no such command exists.
