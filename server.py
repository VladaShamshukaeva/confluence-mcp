#!/usr/bin/env python3
"""Local Confluence MCP server written in Python:
- read/search tools
- create/update page tools constrained to ALLOWED_FOLDER_IDS
- Basic Auth using ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN
- .env loading with override=True so the local .env wins
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from html import unescape
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable
from xml.sax.saxutils import escape
from uuid import uuid4

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
SERVER_VERSION = "3.2.0"
MAX_PAGE_BODY_BYTES = 1 * 1024 * 1024
DRAWIO_BOOTSTRAP_BODY = "<p>Preparing diagram content...</p>"
GENERATED_DIAGRAMS_DIR = PROJECT_ROOT / "generated-diagrams"

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
        "description": "Create a page in one of the allowed Confluence folders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Page title."},
                "body": {
                    "type": "string",
                    "description": "Confluence storage format / XHTML body. Optional when using source inputs.",
                },
                "targetFolderId": {
                    "type": "string",
                    "description": "Optional target folder ID. Defaults to the first allowed folder.",
                },
                "sourcePageId": {
                    "type": "string",
                    "description": "Optional Confluence page ID to use as source material.",
                },
                "sourcePageTitle": {
                    "type": "string",
                    "description": "Optional Confluence page title to use as source material.",
                },
                "sourceFolderId": {
                    "type": "string",
                    "description": "Optional allowed folder ID used to validate the source page location.",
                },
                "javaSourcePaths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional Java file or directory paths to summarize into the page body.",
                },
                "figmaScreenshotPaths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional screenshot paths. On macOS, Vision OCR extracts visible text.",
                },
                "drawIoDiagramPaths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional local .drawio or .xml diagram files to upload and embed with the draw.io Confluence macro.",
                },
                "diagramInstructions": {
                    "type": "string",
                    "description": "Optional natural-language instructions or step list used to generate a draw.io diagram automatically.",
                },
                "diagramSourcePaths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional local file or directory paths whose code or documents should be summarized into the generated diagram.",
                },
                "diagramFileName": {
                    "type": "string",
                    "description": "Optional attachment filename for an auto-generated draw.io diagram. Must end with .drawio or .xml.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_confluence_page",
        "description": "Update a page in one of the allowed Confluence folders.",
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
                    "description": "New Confluence storage format / XHTML body. Optional when using source inputs.",
                },
                "newTitle": {
                    "type": "string",
                    "description": "Optional new page title.",
                },
                "sourcePageId": {
                    "type": "string",
                    "description": "Optional Confluence page ID to use as source material.",
                },
                "sourcePageTitle": {
                    "type": "string",
                    "description": "Optional Confluence page title to use as source material.",
                },
                "sourceFolderId": {
                    "type": "string",
                    "description": "Optional allowed folder ID used to validate the source page location.",
                },
                "javaSourcePaths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional Java file or directory paths to summarize into the page body.",
                },
                "figmaScreenshotPaths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional screenshot paths. On macOS, Vision OCR extracts visible text.",
                },
                "drawIoDiagramPaths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional local .drawio or .xml diagram files to upload and embed with the draw.io Confluence macro.",
                },
                "diagramInstructions": {
                    "type": "string",
                    "description": "Optional natural-language instructions or step list used to generate a draw.io diagram automatically.",
                },
                "diagramSourcePaths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional local file or directory paths whose code or documents should be summarized into the generated diagram.",
                },
                "diagramFileName": {
                    "type": "string",
                    "description": "Optional attachment filename for an auto-generated draw.io diagram. Must end with .drawio or .xml.",
                },
            },
            "anyOf": [
                {"required": ["pageId", "body"]},
                {"required": ["title", "body"]},
                {"required": ["pageId"]},
                {"required": ["title"]}
            ],
        },
    },
]


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    data: str


@dataclass(frozen=True)
class DiagramAttachment:
    attachment_name: str
    local_path: Path


@dataclass(frozen=True)
class PageBodyPlan:
    body: str
    diagram_attachments: list[DiagramAttachment]


@dataclass(frozen=True)
class DiagramNode:
    title: str
    detail: str = ""


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


def require_allowed_folder_ids() -> list[str]:
    raw = read_env("ALLOWED_FOLDER_IDS") or read_env("ALLOWED_FOLDER_ID")
    if not raw:
        raise RuntimeError("ALLOWED_FOLDER_ID or ALLOWED_FOLDER_IDS must be set in .env")

    folder_ids = [item.strip() for item in re.split(r"[,\n]", raw) if item.strip()]
    if not folder_ids:
        raise RuntimeError("No valid folder IDs were found in ALLOWED_FOLDER_ID(S).")
    return folder_ids


def resolve_target_folder_id(explicit_folder_id: str | None = None) -> str:
    allowed_folder_ids = require_allowed_folder_ids()
    if explicit_folder_id:
        folder_id = str(explicit_folder_id).strip()
        if folder_id not in allowed_folder_ids:
            raise RuntimeError(
                f"Requested folder '{folder_id}' is not in ALLOWED_FOLDER_ID(S): {', '.join(allowed_folder_ids)}"
            )
        return folder_id

    return allowed_folder_ids[0]


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


def confluence_put_multipart(
    url: str,
    files: dict[str, Any],
    data: dict[str, Any],
) -> HttpResponse:
    headers = _auth_headers()
    headers["X-Atlassian-Token"] = "nocheck"
    response = requests.put(
        url,
        headers=headers,
        files=files,
        data=data,
        timeout=60,
    )
    return HttpResponse(status_code=response.status_code, data=response.text)


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


def assert_folder_write_allowed(page: dict[str, Any], action: str, folder_id: str | None = None) -> None:
    allowed_folder_ids = [folder_id] if folder_id else require_allowed_folder_ids()
    if not any(is_folder_in_ancestors(page, allowed_folder_id) for allowed_folder_id in allowed_folder_ids):
        raise RuntimeError(
        f"{action} blocked: page is outside ALLOWED_FOLDER_ID(S) '{', '.join(allowed_folder_ids)}'."
        )


def normalize_body(body: str) -> str:
    byte_count = len(body.encode("utf-8"))
    if byte_count > MAX_PAGE_BODY_BYTES:
        raise RuntimeError(
            f"Page body size ({byte_count} bytes) exceeds maximum allowed ({MAX_PAGE_BODY_BYTES} bytes)."
        )
    return body


def get_folder_space_id(folder_id: str) -> str:
    base_url = require_base_url()
    url = f"{base_url}/api/v2/folders/{folder_id}"
    response = confluence_get(url)
    assert_2xx("GET", url, response)
    data = json.loads(response.data)
    space_id = data.get("spaceId")
    if not space_id:
        raise RuntimeError(f"GET {url} failed: response missing 'spaceId'.")
    return str(space_id)


def resolve_source_page(args: dict[str, Any]) -> dict[str, Any] | None:
    page_id = args.get("sourcePageId")
    page_title = args.get("sourcePageTitle")
    if not page_id and not page_title:
        return None

    page = get_confluence_page_by_id(str(page_id)) if page_id else find_confluence_page_by_title(str(page_title))
    source_folder_id = args.get("sourceFolderId")
    assert_folder_write_allowed(page, "source page read", str(source_folder_id) if source_folder_id else None)
    return page


def read_java_source_snippets(source_paths: list[str]) -> list[tuple[str, str]]:
    snippets: list[tuple[str, str]] = []
    for source_path in source_paths:
        path = Path(source_path).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Java source path does not exist: {source_path}")

        java_files = [path] if path.is_file() else sorted(path.rglob("*.java"))
        if not java_files:
            continue

        for java_file in java_files[:25]:
            text = java_file.read_text(encoding="utf-8", errors="ignore")
            summary_lines: list[str] = []

            package_match = re.search(r"^\s*package\s+([\w.]+);", text, re.MULTILINE)
            class_match = re.search(
                r"\b(public\s+)?(class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)",
                text,
            )
            methods = re.findall(
                r"^\s*(?:public|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>,\[\]\s?]+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^;{]*\)\s*(?:throws\s+[^{]+)?\{",
                text,
                re.MULTILINE,
            )

            if package_match:
                summary_lines.append(f"Package: {package_match.group(1)}")
            if class_match:
                summary_lines.append(f"Type: {class_match.group(2)} {class_match.group(3)}")
            if methods:
                summary_lines.append("Public/Protected methods: " + ", ".join(methods[:12]))

            excerpt = "\n".join(summary_lines) if summary_lines else text[:1200]
            snippets.append((java_file.name, excerpt.strip()))

    return snippets


def read_generic_source_snippets(source_paths: list[str]) -> list[tuple[str, str]]:
    snippets: list[tuple[str, str]] = []
    allowed_suffixes = {".java", ".py", ".md", ".txt", ".yml", ".yaml", ".json", ".xml"}
    for source_path in source_paths:
        path = Path(source_path).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Diagram source path does not exist: {source_path}")

        candidate_files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
        for candidate_file in candidate_files[:25]:
            if candidate_file.suffix.lower() not in allowed_suffixes:
                continue
            text = candidate_file.read_text(encoding="utf-8", errors="ignore")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            excerpt = "\n".join(lines[:5])[:600]
            if excerpt:
                snippets.append((candidate_file.name, excerpt))

    return snippets


def normalize_instruction_line(line: str) -> str:
    cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
    return re.sub(r"\s+", " ", cleaned)


def build_diagram_nodes_from_instructions(instructions: str) -> list[DiagramNode]:
    nodes: list[DiagramNode] = []
    for raw_line in instructions.splitlines():
        line = normalize_instruction_line(raw_line)
        if not line:
            continue
        if "->" in line:
            parts = [normalize_instruction_line(part) for part in line.split("->") if normalize_instruction_line(part)]
            nodes.extend(DiagramNode(title=part) for part in parts)
            continue
        nodes.append(DiagramNode(title=line))
    return nodes[:12]


def build_diagram_nodes_from_sources(source_paths: list[str]) -> list[DiagramNode]:
    if not source_paths:
        return []

    java_nodes = [DiagramNode(title=name, detail=snippet) for name, snippet in read_java_source_snippets(source_paths)]
    if java_nodes:
        return java_nodes[:8]

    return [DiagramNode(title=name, detail=snippet) for name, snippet in read_generic_source_snippets(source_paths)[:8]]


def truncate_diagram_detail(detail: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", detail).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def safe_attachment_name(file_name: str | None, default_stem: str) -> str:
    if file_name:
        attachment_name = Path(file_name).name
        if Path(attachment_name).suffix.lower() not in {".drawio", ".xml"}:
            raise RuntimeError("diagramFileName must end with .drawio or .xml.")
        return attachment_name

    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", default_stem).strip("-") or "generated-diagram"
    return f"{stem}.drawio"


def render_generated_drawio_xml(title: str, nodes: list[DiagramNode]) -> str:
    title_xml = escape(title)
    page_width = max(1400, 260 * max(len(nodes), 3))
    root_lines = [
        '<mxCell id="0"/>',
        '<mxCell id="1" parent="0"/>',
        '<mxCell id="title" value="' + title_xml + '" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#0f3d91;fontColor=#ffffff;strokeColor=#0b2f70;fontSize=20;fontStyle=1;arcSize=16;" vertex="1" parent="1"><mxGeometry x="40" y="30" width="420" height="60" as="geometry"/></mxCell>',
    ]

    base_x = 50
    base_y = 170
    step_width = 220
    step_height = 110
    gap = 40
    for index, node in enumerate(nodes, start=1):
        x = base_x + (index - 1) * (step_width + gap)
        value = escape(f"{index}. {node.title}")
        if node.detail:
            value += "&#xa;" + escape(truncate_diagram_detail(node.detail))
        root_lines.append(
            f'<mxCell id="node{index}" value="{value}" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#2563eb;strokeWidth=2;fontSize=13;arcSize=14;spacing=8;" vertex="1" parent="1"><mxGeometry x="{x}" y="{base_y}" width="{step_width}" height="{step_height}" as="geometry"/></mxCell>'
        )
        if index > 1:
            root_lines.append(
                f'<mxCell id="edge{index}" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;endArrow=block;endFill=1;strokeWidth=2;strokeColor=#475569;" edge="1" parent="1" source="node{index - 1}" target="node{index}"><mxGeometry relative="1" as="geometry"/></mxCell>'
            )

    root = "\n        ".join(root_lines)
    return f'''<mxfile host="app.diagrams.net" modified="2026-07-14T00:00:00.000Z" agent="GitHub Copilot" version="24.7.17" type="device">
  <diagram id="generated-diagram" name="{title_xml}">
    <mxGraphModel dx="1600" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{page_width}" pageHeight="900" math="0" shadow="0">
      <root>
        {root}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
'''


def generate_drawio_diagram_attachment(args: dict[str, Any]) -> DiagramAttachment | None:
    instructions = str(args.get("diagramInstructions") or "").strip()
    diagram_source_paths = [str(path) for path in (args.get("diagramSourcePaths") or []) if str(path).strip()]
    if not instructions and not diagram_source_paths:
        return None

    nodes = build_diagram_nodes_from_instructions(instructions) if instructions else []
    if diagram_source_paths:
        nodes.extend(build_diagram_nodes_from_sources(diagram_source_paths))

    deduped_nodes: list[DiagramNode] = []
    seen_titles: set[str] = set()
    for node in nodes:
        title_key = node.title.strip().lower()
        if not title_key or title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        deduped_nodes.append(node)

    if not deduped_nodes:
        raise RuntimeError("diagramInstructions / diagramSourcePaths did not produce any diagram steps.")

    attachment_name = safe_attachment_name(args.get("diagramFileName"), args.get("title") or "generated-diagram")
    GENERATED_DIAGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = GENERATED_DIAGRAMS_DIR / f"{uuid4()}-{attachment_name}"
    file_path.write_text(
        render_generated_drawio_xml(args.get("title") or "Generated Diagram", deduped_nodes[:12]),
        encoding="utf-8",
    )
    return DiagramAttachment(attachment_name=attachment_name, local_path=file_path)


def collect_drawio_diagram_attachments(source_paths: list[str]) -> list[DiagramAttachment]:
    attachments: list[DiagramAttachment] = []
    seen_names: set[str] = set()
    for source_path in source_paths:
        path = Path(source_path).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"draw.io diagram path does not exist: {source_path}")
        if not path.is_file():
            raise RuntimeError(f"draw.io diagram path must be a file: {source_path}")

        suffix = path.suffix.lower()
        if suffix not in {".drawio", ".xml"}:
            raise RuntimeError(
                f"Unsupported draw.io diagram file '{path.name}'. Expected a .drawio or .xml file."
            )

        attachment_name = path.name
        if attachment_name in seen_names:
            raise RuntimeError(
                f"Duplicate draw.io attachment name '{attachment_name}'. Rename one of the local files first."
            )

        seen_names.add(attachment_name)
        attachments.append(DiagramAttachment(attachment_name=attachment_name, local_path=path))

    return attachments


def build_drawio_macro(diagram: DiagramAttachment) -> str:
    diagram_name = escape(diagram.attachment_name)
    return (
        '<ac:structured-macro ac:name="drawio" ac:schema-version="1">'
        f'<ac:parameter ac:name="diagramName">{diagram_name}</ac:parameter>'
        f'<ac:parameter ac:name="attachment">{diagram_name}</ac:parameter>'
        "</ac:structured-macro>"
    )


def upload_attachment_to_page(page_id: str, local_path: Path, comment: str) -> dict[str, Any]:
    base_url = require_base_url()
    url = f"{base_url}/rest/api/content/{page_id}/child/attachment"
    with local_path.open("rb") as file_handle:
        files = {"file": (local_path.name, file_handle)}
        data = {"minorEdit": "true", "comment": comment}
        response = confluence_put_multipart(url, files=files, data=data)

    assert_2xx("PUT", url, response)
    payload = json.loads(response.data)
    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"PUT {url} succeeded but returned no attachment results for '{local_path.name}'.")
    return results[0]


def upload_drawio_attachments(page_id: str, diagrams: list[DiagramAttachment]) -> list[dict[str, Any]]:
    uploaded: list[dict[str, Any]] = []
    for diagram in diagrams:
        uploaded.append(
            upload_attachment_to_page(
                page_id,
                diagram.local_path,
                comment=f"Uploaded by confluence-local-mcp for draw.io embedding: {diagram.attachment_name}",
            )
        )
    return uploaded


def ocr_screenshot(image_path: str) -> str:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"Screenshot path does not exist: {image_path}")

    swift_code = '''
import AppKit
import Foundation
import Vision

let inputPath = CommandLine.arguments[1]
let imageUrl = URL(fileURLWithPath: inputPath)
guard let image = NSImage(contentsOf: imageUrl) else {
    fputs("Unable to open image\n", stderr)
    exit(2)
}

guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let cgImage = bitmap.cgImage else {
    fputs("Unable to decode image\n", stderr)
    exit(3)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])

let lines = (request.results ?? []).compactMap { observation in
    observation.topCandidates(1).first?.string
}
print(lines.joined(separator: "\n"))
'''

    with NamedTemporaryFile("w", suffix=".swift", encoding="utf-8", delete=True) as script_file:
        script_file.write(swift_code)
        script_file.flush()
        result = subprocess.run(
            ["/usr/bin/swift", script_file.name, str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"OCR failed for screenshot '{image_path}': {result.stderr.strip() or result.stdout.strip() or 'unknown error'}"
        )

    return result.stdout.strip()


def build_page_body_plan(args: dict[str, Any]) -> PageBodyPlan:
    diagram_attachments = collect_drawio_diagram_attachments(
        [str(path) for path in (args.get("drawIoDiagramPaths") or []) if str(path).strip()]
    )
    generated_diagram = generate_drawio_diagram_attachment(args)
    if generated_diagram:
        if any(diagram.attachment_name == generated_diagram.attachment_name for diagram in diagram_attachments):
            raise RuntimeError(
                f"Generated diagram attachment '{generated_diagram.attachment_name}' conflicts with an existing drawIoDiagramPaths filename."
            )
        diagram_attachments.append(generated_diagram)
    body_value = str(args["body"]).strip() if args.get("body") else ""
    sections: list[str] = [body_value] if body_value else []

    source_page = resolve_source_page(args)
    if source_page:
        title = source_page.get("title") or "Source Page"
        body_value = (((source_page.get("body") or {}).get("storage") or {}).get("value") or "").strip()
        sections.append(f"<h2>Source Document</h2><p><strong>{title}</strong></p>{body_value}")

    figma_screenshot_paths = [str(path) for path in (args.get("figmaScreenshotPaths") or []) if str(path).strip()]
    if figma_screenshot_paths:
        screenshot_sections: list[str] = []
        for screenshot_path in figma_screenshot_paths:
            extracted_text = ocr_screenshot(screenshot_path)
            screenshot_sections.append(
                "<h3>Screenshot OCR</h3>"
                + "<ac:structured-macro ac:name=\"code\"><ac:plain-text-body><![CDATA["
                + extracted_text
                + "]]></ac:plain-text-body></ac:structured-macro>"
            )
        sections.append("<h2>Figma Screenshot Notes</h2>" + "".join(screenshot_sections))

    java_source_paths = [str(path) for path in (args.get("javaSourcePaths") or []) if str(path).strip()]
    if java_source_paths:
        java_snippets = read_java_source_snippets(java_source_paths)
        snippet_blocks = []
        for name, snippet in java_snippets:
            snippet_blocks.append(
                f"<h3>{name}</h3>"
                + "<ac:structured-macro ac:name=\"code\"><ac:plain-text-body><![CDATA["
                + snippet
                + "]]></ac:plain-text-body></ac:structured-macro>"
            )
        sections.append("<h2>Java Source Summary</h2>" + "".join(snippet_blocks))

    if diagram_attachments:
        diagram_blocks = [f"<h3>{escape(diagram.attachment_name)}</h3>{build_drawio_macro(diagram)}" for diagram in diagram_attachments]
        sections.append("<h2>Draw.io Diagrams</h2>" + "".join(diagram_blocks))

    if not sections:
        raise RuntimeError(
            "create/update requires either 'body' or at least one source input: sourcePageId/sourcePageTitle, figmaScreenshotPaths, javaSourcePaths, drawIoDiagramPaths, diagramInstructions, diagramSourcePaths."
        )

    return PageBodyPlan(body=normalize_body("".join(sections)), diagram_attachments=diagram_attachments)


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
    target_folder_id = resolve_target_folder_id(args.get("targetFolderId"))
    space_id = get_folder_space_id(target_folder_id)
    body_plan = build_page_body_plan(args)
    initial_body = body_plan.body if not body_plan.diagram_attachments else DRAWIO_BOOTSTRAP_BODY

    v2_payload = {
        "type": "page",
        "title": args["title"],
        "spaceId": space_id,
        "body": {"representation": "storage", "value": initial_body},
    }

    create_url = f"{base_url}/api/v2/pages"
    response = confluence_post_json(create_url, v2_payload)
    assert_2xx("POST", create_url, response)

    new_page_data = json.loads(response.data)
    new_page_id = new_page_data.get("id")
    if not new_page_id:
        raise RuntimeError(f"POST {create_url} failed: response missing created page id.")

    move_url = f"{base_url}/rest/api/content/{new_page_id}/move/append/{target_folder_id}"
    move_response = confluence_request("PUT", move_url)
    assert_2xx("PUT", move_url, move_response)

    if body_plan.diagram_attachments:
        upload_drawio_attachments(str(new_page_id), body_plan.diagram_attachments)
        page = get_confluence_page_by_id(str(new_page_id))
        update_payload = {
            "id": page.get("id"),
            "type": "page",
            "title": page.get("title"),
            "space": {"key": (page.get("space") or {}).get("key")},
            "version": {"number": int((page.get("version") or {}).get("number", 0)) + 1},
            "body": {
                "storage": {
                    "value": body_plan.body,
                    "representation": "storage",
                }
            },
        }
        finalize_url = f"{base_url}/rest/api/content/{new_page_id}"
        finalize_response = confluence_put_json(finalize_url, update_payload)
        assert_2xx("PUT", finalize_url, finalize_response)

    return json.dumps(
        {
            "id": new_page_id,
            "title": new_page_data.get("title"),
            "spaceId": space_id,
            "folderId": target_folder_id,
            "url": f"{base_url.replace('/wiki', '')}{(new_page_data.get('_links') or {}).get('webui')}"
            if (new_page_data.get("_links") or {}).get("webui")
            else f"{base_url}{(new_page_data.get('_links') or {}).get('self', '')}",
        },
        indent=2,
    )


def update_confluence_page(args: dict[str, Any]) -> str:
    base_url = require_base_url()
    body_plan = build_page_body_plan(args)

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

    if body_plan.diagram_attachments:
        upload_drawio_attachments(str(page.get("id")), body_plan.diagram_attachments)

    payload = {
        "id": page.get("id"),
        "type": "page",
        "title": args.get("newTitle") or page.get("title"),
        "space": {"key": (page.get("space") or {}).get("key")},
        "version": {"number": int((page.get("version") or {}).get("number", 0)) + 1},
        "body": {
            "storage": {
                "value": body_plan.body,
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
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
    sys.stdout.write(data + "\n")
    sys.stdout.flush()


def send_result(message_id: Any, result: Any) -> None:
    send_message({"jsonrpc": "2.0", "id": message_id, "result": result})


def send_error(message_id: Any, code: int, message: str, data: Any | None = None) -> None:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    send_message({"jsonrpc": "2.0", "id": message_id, "error": error})


def read_message() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
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
        f"ALLOWED_FOLDER_IDS={os.getenv('ALLOWED_FOLDER_IDS') or os.getenv('ALLOWED_FOLDER_ID', '(not set)')}\n"
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
