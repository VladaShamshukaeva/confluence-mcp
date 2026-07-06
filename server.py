#!/usr/bin/env python3
"""Local Confluence MCP server written in Python:
- read/search tools
- create/update page tools constrained to ALLOWED_FOLDER_ID
- Basic Auth using ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN
- .env loading with override=True so the local .env wins
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from html import unescape
from io import StringIO
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


def load_project_dotenv() -> None:
    dotenv_path = PROJECT_ROOT / ".env"
    if not dotenv_path.exists():
        return

    # Keep comments and normal key=value pairs, but skip malformed lines so a
    # stray local entry does not break server startup or spam warnings.
    valid_assignment = re.compile(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=.*$")
    filtered_lines: list[str] = []
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or valid_assignment.match(line):
            filtered_lines.append(line)

    load_dotenv(stream=StringIO("\n".join(filtered_lines) + "\n"), override=True)


load_project_dotenv()

SERVER_NAME = "confluence-local-mcp"
SERVER_VERSION = "3.0.0"
MAX_PAGE_BODY_BYTES = 1 * 1024 * 1024

ALLOWED_TOOLS = [
    "search_confluence_pages",
    "read_confluence_page",
    "get_page_children",
    "create_confluence_page",
    "update_confluence_page",
]

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_confluence_pages",
        "description": "Search Confluence pages by keyword, topic, or business term. Use for finding, discovering, or listing pages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search term."},
                "spaceKey": {
                    "type": "string",
                    "description": "Optional Confluence space key to restrict the search (e.g. 'INV', 'BSAPPS').",
                },
                "limit": {
                    "type": "number",
                    "description": "Max results to return. Default: 10. Maximum: 50.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_confluence_page",
        "description": "Read the full content of a Confluence page by its numeric page ID. Use for reading, summarizing, or answering questions about a page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pageId": {
                    "type": "string",
                    "description": "The numeric Confluence page ID.",
                },
                "format": {
                    "type": "string",
                    "enum": ["plainText", "storage"],
                    "description": "'plainText' (default) returns readable text. 'storage' returns raw Confluence XML.",
                },
            },
            "required": ["pageId"],
        },
    },
    {
        "name": "get_page_children",
        "description": "Return direct child pages of a given Confluence page. Use for documentation structure or nested pages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pageId": {
                    "type": "string",
                    "description": "The parent page ID whose children to retrieve.",
                },
                "limit": {
                    "type": "number",
                    "description": "Max children to return. Default: 25. Maximum: 100.",
                },
            },
            "required": ["pageId"],
        },
    },
    {
        "name": "create_confluence_page",
        "description": "Create a page in ALLOWED_FOLDER_ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Page title."},
                "body": {
                    "type": "string",
                    "description": "Confluence storage format / XHTML body.",
                },
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "update_confluence_page",
        "description": "Update a page in ALLOWED_FOLDER_ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pageId": {
                    "type": "string",
                    "description": "The page ID to update.",
                },
                "title": {
                    "type": "string",
                    "description": "Resolve page by title if pageId is omitted.",
                },
                "body": {
                    "type": "string",
                    "description": "New Confluence storage format / XHTML body.",
                },
                "newTitle": {
                    "type": "string",
                    "description": "Optional new page title.",
                },
            },
            "required": ["body"],
        },
    },
]


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    data: str


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# ============================================================================
# ENVIRONMENT
# ============================================================================

def read_env(name: str) -> str | None:
    raw = os.getenv(name)
    if not raw:
        return None
    # Accept values like '"BSAPPS"' or '"BSAPPS",' from accidental JSON-like env formatting.
    return raw.strip().strip("\"'").rstrip(",").strip()


def require_allowed_folder_id() -> str:
    folder_id = read_env("ALLOWED_FOLDER_ID")
    if not folder_id:
        raise RuntimeError("ALLOWED_FOLDER_ID is not set in .env")
    return folder_id


def require_base_url() -> str:
    base_url = read_env("CONFLUENCE_BASE_URL")
    if not base_url:
        raise RuntimeError("CONFLUENCE_BASE_URL is not set in .env")
    return base_url


# ============================================================================
# HTTP HELPERS
# ============================================================================

def _auth_headers() -> dict[str, str]:
    email = read_env("ATLASSIAN_EMAIL")
    token = read_env("ATLASSIAN_API_TOKEN")
    if not email or not token:
        raise RuntimeError("Missing ATLASSIAN_EMAIL or ATLASSIAN_API_TOKEN in .env")

    basic_auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {basic_auth}",
        "Accept": "application/json",
        "User-Agent": "confluence-local-mcp/3.0",
    }


def confluence_request(
    method: str,
    url: str,
    payload: Any | None = None,
) -> HttpResponse:
    headers = _auth_headers()
    body = None if payload is None else json.dumps(payload)
    if body is not None:
        headers["Content-Type"] = "application/json"

    response = requests.request(
        method=method,
        url=url,
        headers=headers,
        data=body,
        timeout=30,
    )
    return HttpResponse(status_code=response.status_code, data=response.text)


def confluence_get(url: str) -> HttpResponse:
    return confluence_request("GET", url)


def confluence_post_json(url: str, payload: Any) -> HttpResponse:
    return confluence_request("POST", url, payload)


def confluence_put_json(url: str, payload: Any) -> HttpResponse:
    return confluence_request("PUT", url, payload)


def throw_http_error(method: str, url: str, status_code: int, response_body: str) -> None:
    raise RuntimeError(
        f"{method} {url} failed: HTTP {status_code}\n{response_body or '(empty response body)'}"
    )


def assert_2xx(method: str, url: str, response: HttpResponse) -> None:
    if response.status_code < 200 or response.status_code >= 300:
        throw_http_error(method, url, response.status_code, response.data)


def html_to_plain_text(html_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = unescape(text)
    text = re.sub(r"\s{2,}", "\n", text)
    return text.strip()


def auth_error_message(code: int) -> str | None:
    if code == 401:
        return "Authentication failed (401). Token may be expired."
    if code == 403:
        return "Access denied (403)."
    if code == 404:
        return "Not found (404). Check that the page ID or space key is correct."
    return None


# ============================================================================
# CONFLUENCE HELPERS
# ============================================================================

def get_confluence_page_by_id(page_id: str) -> dict[str, Any]:
    base_url = require_base_url()
    url = f"{base_url}/rest/api/content/{page_id}?expand=space,version,ancestors,body.storage"
    response = confluence_get(url)
    err = auth_error_message(response.status_code)
    if err:
        raise RuntimeError(err)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}\n{response.data[:300]}")
    return json.loads(response.data)


def find_confluence_page_by_title(title: str) -> dict[str, Any]:
    base_url = require_base_url()
    escaped = title.replace('"', '\\"')
    cql = f'type = page AND title = "{escaped}" ORDER BY lastmodified DESC'
    url = (
        f"{base_url}/rest/api/content/search?cql={requests.utils.quote(cql)}"
        f"&limit=2&expand=space,version,ancestors,body.storage"
    )
    response = confluence_get(url)
    err = auth_error_message(response.status_code)
    if err:
        raise RuntimeError(err)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}\n{response.data[:300]}")
    data = json.loads(response.data)
    results = data.get("results") or []
    if len(results) == 0:
        raise RuntimeError(f"Page titled '{title}' not found.")
    if len(results) > 1:
        raise RuntimeError(f"Multiple pages titled '{title}' found. Use pageId instead.")
    return results[0]


def is_folder_in_ancestors(page: dict[str, Any], folder_id: str) -> bool:
    folder = str(folder_id)

    # v1 shape: ancestors contains the full parent chain.
    ancestors = [str(a.get("id")) for a in (page.get("ancestors") or []) if a.get("id") is not None]
    if folder in ancestors:
        return True

    # v2 shape may include parentType/parentId.
    parent_type = str(page.get("parentType") or page.get("parent", {}).get("type") or "").lower()
    parent_id = str(page.get("parentId") or page.get("parent", {}).get("id") or "")
    if parent_type == "folder" and parent_id == folder:
        return True

    return False


def assert_folder_write_allowed(page: dict[str, Any], action: str) -> None:
    allowed_folder_id = require_allowed_folder_id()
    if not is_folder_in_ancestors(page, allowed_folder_id):
        raise RuntimeError(
        f"{action} blocked: page is outside ALLOWED_FOLDER_ID '{allowed_folder_id}'."
        )


def normalize_body(body: str) -> str:
    byte_count = len(body.encode("utf-8"))
    if byte_count > MAX_PAGE_BODY_BYTES:
        raise RuntimeError(
            f"Page body size ({byte_count} bytes) exceeds maximum allowed ({MAX_PAGE_BODY_BYTES} bytes)."
        )
    return body


def get_allowed_folder_space_id() -> str:
    base_url = require_base_url()
    folder_id = require_allowed_folder_id()
    url = f"{base_url}/api/v2/folders/{folder_id}"
    response = confluence_get(url)
    assert_2xx("GET", url, response)
    data = json.loads(response.data)
    space_id = data.get("spaceId")
    if not space_id:
        raise RuntimeError(f"GET {url} failed: response missing 'spaceId'.")
    return str(space_id)


# ============================================================================
# TOOL IMPLEMENTATIONS
# ============================================================================

def search_confluence_pages(args: dict[str, Any]) -> str:
    base_url = require_base_url()
    limit_value = args.get("limit")
    limit = min(int(limit_value) if limit_value is not None else 10, 50)

    query = str(args["query"]).replace('"', '\\"')
    cql = f'text ~ "{query}" AND type = page'
    if args.get("spaceKey"):
        space_key = str(args["spaceKey"]).replace('"', '\\"')
        cql += f' AND space.key = "{space_key}"'
    cql += " ORDER BY lastmodified DESC"

    url = (
        f"{base_url}/rest/api/content/search"
        f"?cql={requests.utils.quote(cql)}"
        f"&limit={limit}"
        f"&expand=space,version"
    )

    response = confluence_get(url)
    err = auth_error_message(response.status_code)
    if err:
        return f"Error: {err}"
    if response.status_code != 200:
        return f"Error: HTTP {response.status_code}\n{response.data[:300]}"

    data = json.loads(response.data)
    pages = [
        {
            "id": p.get("id"),
            "title": p.get("title"),
            "space": (p.get("space") or {}).get("name", "Unknown"),
            "spaceKey": (p.get("space") or {}).get("key", ""),
            "version": (p.get("version") or {}).get("number"),
            "url": f"{base_url.replace('/wiki', '')}{(p.get('_links') or {}).get('webui')}"
            if (p.get("_links") or {}).get("webui")
            else "N/A",
        }
        for p in (data.get("results") or [])
    ]

    return json.dumps(
        {"total": data.get("totalSize", len(pages)), "cqlUsed": cql, "results": pages},
        indent=2,
    )


def read_confluence_page(args: dict[str, Any]) -> str:
    base_url = require_base_url()
    fmt = args.get("format") or "plainText"

    url = (
        f"{base_url}/rest/api/content/{args['pageId']}"
        f"?expand=space,version,body.storage,body.view,ancestors"
    )

    response = confluence_get(url)
    err = auth_error_message(response.status_code)
    if err:
        return f"Error: {err}"
    if response.status_code != 200:
        return f"Error: HTTP {response.status_code}\n{response.data[:300]}"

    page = json.loads(response.data)
    raw_body = (
        ((page.get("body") or {}).get("storage") or {}).get("value", "")
        if fmt == "storage"
        else ((page.get("body") or {}).get("view") or {}).get("value")
        or ((page.get("body") or {}).get("storage") or {}).get("value", "")
    )
    body = html_to_plain_text(raw_body) if fmt == "plainText" else raw_body
    ancestors = [a.get("title") for a in (page.get("ancestors") or [])]

    return json.dumps(
        {
            "id": page.get("id"),
            "title": page.get("title"),
            "space": (page.get("space") or {}).get("name", "Unknown"),
            "spaceKey": (page.get("space") or {}).get("key", ""),
            "version": (page.get("version") or {}).get("number"),
            "status": page.get("status"),
            "ancestors": ancestors,
            "url": f"{base_url.replace('/wiki', '')}{(page.get('_links') or {}).get('webui')}"
            if (page.get("_links") or {}).get("webui")
            else "N/A",
            "format": fmt,
            "body": body,
        },
        indent=2,
    )


def get_page_children(args: dict[str, Any]) -> str:
    base_url = require_base_url()
    limit_value = args.get("limit")
    limit = min(int(limit_value) if limit_value is not None else 25, 100)

    url = f"{base_url}/rest/api/content/{args['pageId']}/child/page?limit={limit}&expand=version,space"
    response = confluence_get(url)
    err = auth_error_message(response.status_code)
    if err:
        return f"Error: {err}"
    if response.status_code != 200:
        return f"Error: HTTP {response.status_code}\n{response.data[:300]}"

    data = json.loads(response.data)
    children = [
        {
            "id": p.get("id"),
            "title": p.get("title"),
            "space": (p.get("space") or {}).get("name", "Unknown"),
            "spaceKey": (p.get("space") or {}).get("key", ""),
            "version": (p.get("version") or {}).get("number"),
            "url": f"{base_url.replace('/wiki', '')}{(p.get('_links') or {}).get('webui')}"
            if (p.get("_links") or {}).get("webui")
            else "N/A",
        }
        for p in (data.get("results") or [])
    ]

    return json.dumps(
        {"parentId": args["pageId"], "total": data.get("size", len(children)), "children": children},
        indent=2,
    )


def create_confluence_page(args: dict[str, Any]) -> str:
    base_url = require_base_url()
    allowed_folder_id = require_allowed_folder_id()
    space_id = get_allowed_folder_space_id()

    v2_payload = {
        "type": "page",
        "title": args["title"],
        "spaceId": space_id,
        "body": {"representation": "storage", "value": normalize_body(args["body"])},
    }

    create_url = f"{base_url}/api/v2/pages"
    response = confluence_post_json(create_url, v2_payload)
    assert_2xx("POST", create_url, response)

    new_page_data = json.loads(response.data)
    new_page_id = new_page_data.get("id")
    if not new_page_id:
        raise RuntimeError(f"POST {create_url} failed: response missing created page id.")

    move_url = f"{base_url}/rest/api/content/{new_page_id}/move/append/{allowed_folder_id}"
    move_response = confluence_request("PUT", move_url)
    assert_2xx("PUT", move_url, move_response)

    return json.dumps(
        {
            "id": new_page_id,
            "title": new_page_data.get("title"),
            "spaceId": space_id,
            "folderId": allowed_folder_id,
            "url": f"{base_url.replace('/wiki', '')}{(new_page_data.get('_links') or {}).get('webui')}"
            if (new_page_data.get("_links") or {}).get("webui")
            else f"{base_url}{(new_page_data.get('_links') or {}).get('self', '')}",
        },
        indent=2,
    )


def update_confluence_page(args: dict[str, Any]) -> str:
    base_url = require_base_url()

    resolved_page = None
    if args.get("pageId"):
        resolved_page = get_confluence_page_by_id(str(args["pageId"]))
    elif args.get("title"):
        resolved_page = find_confluence_page_by_title(str(args["title"]))

    if not resolved_page:
        raise RuntimeError("update_confluence_page requires either pageId or title.")

    # Always re-read with ancestors to enforce descendant-of-folder guard reliably.
    page = get_confluence_page_by_id(str(resolved_page.get("id")))
    assert_folder_write_allowed(page, "update_confluence_page")

    payload = {
        "id": page.get("id"),
        "type": "page",
        "title": args.get("newTitle") or page.get("title"),
        "space": {"key": (page.get("space") or {}).get("key")},
        "version": {"number": int((page.get("version") or {}).get("number", 0)) + 1},
        "body": {
            "storage": {
                "value": normalize_body(args["body"]),
                "representation": "storage",
            }
        },
    }

    update_url = f"{base_url}/rest/api/content/{page.get('id')}"
    response = confluence_put_json(update_url, payload)
    assert_2xx("PUT", update_url, response)

    data = json.loads(response.data)
    return json.dumps(
        {
            "id": data.get("id"),
            "title": data.get("title"),
            "spaceKey": (data.get("space") or {}).get("key"),
            "version": (data.get("version") or {}).get("number"),
            "url": f"{base_url.replace('/wiki', '')}{(data.get('_links') or {}).get('webui')}"
            if (data.get("_links") or {}).get("webui")
            else "N/A",
        },
        indent=2,
    )


# ============================================================================
# TOOL REGISTRY / ALLOWLIST
# ============================================================================

def assert_tool_allowlist() -> None:
    registered = sorted(tool["name"] for tool in TOOLS)
    allowed = sorted(ALLOWED_TOOLS)
    if registered != allowed:
        raise RuntimeError(f"Tool allowlist violation. Registered={registered}, Allowed={allowed}")


def is_allowed_tool(name: str) -> bool:
    return name in ALLOWED_TOOLS


def call_tool(name: str, args: dict[str, Any] | None) -> str:
    payload = args or {}

    if name == "search_confluence_pages":
        return search_confluence_pages(payload)
    if name == "read_confluence_page":
        return read_confluence_page(payload)
    if name == "get_page_children":
        return get_page_children(payload)
    if name == "create_confluence_page":
        return create_confluence_page(payload)
    if name == "update_confluence_page":
        return update_confluence_page(payload)

    raise RuntimeError(f"Unknown tool: {name}")


# ============================================================================
# MCP / JSON-RPC TRANSPORT
# ============================================================================

def send_message(message: dict[str, Any]) -> None:
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def send_result(message_id: Any, result: Any) -> None:
    send_message({"jsonrpc": "2.0", "id": message_id, "result": result})


def send_error(message_id: Any, code: int, message: str, data: Any | None = None) -> None:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    send_message({"jsonrpc": "2.0", "id": message_id, "error": error})


def read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        try:
            key, value = line.decode("utf-8", errors="replace").split(":", 1)
        except ValueError:
            continue
        headers[key.strip().lower()] = value.strip()

    content_length = headers.get("content-length")
    if not content_length:
        raise JsonRpcError(-32600, "Invalid Request", {"reason": "Missing Content-Length header"})

    body = sys.stdin.buffer.read(int(content_length))
    if not body:
        raise JsonRpcError(-32700, "Parse error", {"reason": "Empty message body"})

    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise JsonRpcError(-32700, "Parse error", {"reason": str(exc)}) from exc


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        protocol_version = params.get("protocolVersion") or "2024-11-05"
        return {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    if method == "initialized":
        return None

    if method == "ping":
        return {}

    if method == "tools/list":
        return {"tools": TOOLS}

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        sys.stderr.write(f"[MCP] {tool_name} {json.dumps(tool_args, ensure_ascii=False)}\n")
        sys.stderr.flush()

        if not is_allowed_tool(str(tool_name)):
            return {"content": [{"type": "text", "text": f"Unsupported tool: {tool_name}"}], "isError": True}

        try:
            result = call_tool(str(tool_name), tool_args)
            return {"content": [{"type": "text", "text": result}]}
        except Exception as exc:  # noqa: BLE001 - intentionally converting to MCP tool error
            msg = str(exc)
            sys.stderr.write(f"[MCP] Error in {tool_name}: {msg}\n")
            sys.stderr.flush()
            return {"content": [{"type": "text", "text": f"Error: {msg}"}], "isError": True}

    if message_id is None:
        return None

    raise JsonRpcError(-32601, "Method not found", {"method": method})


def main() -> int:
    assert_tool_allowlist()
    sys.stderr.write(
        f"[MCP] Server v{SERVER_VERSION} started. Tools: {', '.join(tool['name'] for tool in TOOLS)}. "
        f"ALLOWED_FOLDER_ID={os.getenv('ALLOWED_FOLDER_ID', '(not set)')}\n"
    )
    sys.stderr.flush()

    while True:
        try:
            message = read_message()
        except JsonRpcError as exc:
            send_error(None, exc.code, exc.message, exc.data)
            continue
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[MCP] Fatal read error: {exc}\n")
            sys.stderr.flush()
            return 1

        if message is None:
            return 0

        message_id = message.get("id")
        try:
            result = handle_request(message)
            if message_id is not None and result is not None:
                send_result(message_id, result)
        except JsonRpcError as exc:
            if message_id is not None:
                send_error(message_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[MCP] Fatal error: {exc}\n")
            sys.stderr.flush()
            if message_id is not None:
                send_error(message_id, -32603, "Internal error", {"message": str(exc)})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
