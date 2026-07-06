# Strict MCP server rules

This project is a local read-only Confluence MCP server.

The only allowed MCP tools are:
`search_confluence_pages`
`read_confluence_page`
`get_page_children`

Do not create any new MCP tools.

Do not register any new `server.tool(...)` calls.

Do not add query-specific tools.

Do not add write tools.

Do not add raw REST passthrough tools.

Do not bypass the MCP tools with direct `curl` or raw Confluence REST API calls for user-facing behavior.

Use terminal/curl only when explicitly debugging authentication or server startup.

For user-facing requests:
Search/find/discover docs -> use `search_confluence_pages`
Read/open/summarize/explain page by ID -> use `read_confluence_page`
Show nested/child pages -> use `get_page_children`

If the request cannot be mapped to one of these tools, reject it clearly and do not implement anything new.

