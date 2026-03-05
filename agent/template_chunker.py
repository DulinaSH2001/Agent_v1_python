"""
Antigravity Agent - Template Chunker

Splits template files into semantically meaningful chunks for RAG retrieval.

Chunking strategies:
- .tsx/.ts: Split on top-level exports, component definitions, function declarations
- .json: Split by top-level keys (dependencies, scripts, etc.)
- .css: Split by comment-delimited sections or treat as single chunk
- Config files: Treat as single chunk (small files)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.template_loader import load_template, should_skip_file

logger = logging.getLogger(__name__)


@dataclass
class TemplateChunk:
    """A semantically meaningful chunk of a template file."""
    chunk_id: str
    file_path: str
    content: str
    chunk_type: str  # "component" | "function" | "config" | "style" | "import_block" | "type" | "full_file" | "usage_example"
    metadata: Dict[str, Any] = field(default_factory=dict)
    template_name: str = "nextjs-app"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Files small enough to keep as single chunks
SINGLE_CHUNK_FILES = {
    "next.config.js",
    "tailwind.config.js",
    "tsconfig.json",
    "template.json",
    "README.md",
}

# Regex patterns for splitting TypeScript/TSX files
_EXPORT_PATTERN = re.compile(
    r'^(?:export\s+(?:default\s+)?(?:function|const|class|interface|type|enum|async\s+function)\s+\w+)',
    re.MULTILINE,
)
_CONST_CVA_PATTERN = re.compile(
    r'^const\s+(\w+)\s*=\s*cva\(',
    re.MULTILINE,
)
_COMPONENT_PATTERN = re.compile(
    r'^(?:const|function)\s+(\w+)\s*(?::\s*\w+\s*)?=?\s*(?:React\.forwardRef|React\.memo|\()',
    re.MULTILINE,
)


def chunk_template(template_name: str = "nextjs-app") -> List[TemplateChunk]:
    """
    Load a template and split all files into semantically meaningful chunks.

    Args:
        template_name: Name of the template directory.

    Returns:
        List of TemplateChunk objects.
    """
    file_system = load_template(template_name)
    chunks: List[TemplateChunk] = []

    for file_path, content in file_system.items():
        if not content or not content.strip():
            continue

        file_chunks = _chunk_file(file_path, content, template_name)
        chunks.extend(file_chunks)

    logger.info(f"Chunked template '{template_name}': {len(file_system)} files -> {len(chunks)} chunks")
    return chunks


def _chunk_file(file_path: str, content: str, template_name: str) -> List[TemplateChunk]:
    """Dispatch to the appropriate chunking strategy based on file extension."""
    basename = Path(file_path).name

    # Small config files: single chunk
    if basename in SINGLE_CHUNK_FILES:
        return [_make_chunk(
            file_path, content, "config",
            template_name=template_name,
            metadata={"is_config": True},
        )]

    ext = Path(file_path).suffix.lower()

    if ext in (".tsx", ".ts", ".jsx", ".js"):
        return _chunk_typescript(file_path, content, template_name)
    elif ext == ".json":
        return _chunk_json(file_path, content, template_name)
    elif ext == ".css":
        return _chunk_css(file_path, content, template_name)
    else:
        # Unknown extension: single chunk
        return [_make_chunk(
            file_path, content, "full_file",
            template_name=template_name,
        )]


def _extract_jsdoc_example(content: str, file_path: str, template_name: str) -> Optional[TemplateChunk]:
    """
    Extract a leading JSDoc block containing @example as a dedicated 'usage_example' chunk.
    Returns None if no such block exists.
    """
    match = re.match(r'(/\*\*.*?\*/)', content, re.DOTALL)
    if not match or '@example' not in match.group(1):
        return None

    jsdoc = match.group(1)
    component_match = re.search(r'@component\s+(\w+)', jsdoc)
    component_name = component_match.group(1) if component_match else Path(file_path).stem

    return TemplateChunk(
        chunk_id=f"{template_name}:{file_path}:usage_example",
        file_path=file_path,
        content=jsdoc,
        chunk_type="usage_example",
        metadata={"component_name": component_name, "is_example": True},
        template_name=template_name,
    )


def _chunk_typescript(file_path: str, content: str, template_name: str) -> List[TemplateChunk]:
    """
    Chunk a TypeScript/TSX file by top-level declarations.

    Strategy:
    1. Extract the import block at the top.
    2. Split remaining content on top-level export/const/function boundaries.
    3. If the file is small (< 40 lines), keep as single chunk.
    """
    # Extract @example JSDoc block first (usage_example chunk)
    example_chunk = _extract_jsdoc_example(content, file_path, template_name)

    lines = content.split("\n")

    # Small files: single chunk (+ usage_example if present)
    if len(lines) < 40:
        chunk_type = _infer_ts_chunk_type(file_path, content)
        result = [_make_chunk(
            file_path, content, chunk_type,
            template_name=template_name,
            metadata={"line_count": len(lines)},
        )]
        if example_chunk:
            result.insert(0, example_chunk)
        return result

    chunks: List[TemplateChunk] = []
    if example_chunk:
        chunks.append(example_chunk)

    # Find import block boundary
    import_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from ") or stripped == "" or stripped.startswith("//"):
            import_end = i + 1
        else:
            break

    # Import block chunk (if substantial)
    if import_end > 2:
        import_content = "\n".join(lines[:import_end])
        chunks.append(_make_chunk(
            file_path, import_content, "import_block",
            template_name=template_name,
            metadata={"start_line": 1, "end_line": import_end},
            suffix="imports",
        ))

    # Find split points in the remaining content
    remaining = "\n".join(lines[import_end:])
    split_points = _find_ts_split_points(remaining)

    if not split_points:
        # No split points found: emit the body as a single chunk
        chunk_type = _infer_ts_chunk_type(file_path, content)
        chunks.append(_make_chunk(
            file_path, remaining, chunk_type,
            template_name=template_name,
            metadata={"start_line": import_end + 1, "end_line": len(lines)},
            suffix="body",
        ))
    else:
        # Split on declaration boundaries
        remaining_lines = remaining.split("\n")
        prev_start = 0
        for idx, (line_offset, name) in enumerate(split_points):
            if idx > 0:
                chunk_content = "\n".join(remaining_lines[prev_start:line_offset]).strip()
                if chunk_content:
                    prev_name = split_points[idx - 1][1]
                    chunks.append(_make_chunk(
                        file_path, chunk_content,
                        _infer_ts_chunk_type(file_path, chunk_content),
                        template_name=template_name,
                        metadata={
                            "start_line": import_end + prev_start + 1,
                            "end_line": import_end + line_offset,
                            "declaration": prev_name,
                        },
                        suffix=prev_name,
                    ))
            prev_start = line_offset

        # Last segment
        chunk_content = "\n".join(remaining_lines[prev_start:]).strip()
        if chunk_content:
            last_name = split_points[-1][1]
            chunks.append(_make_chunk(
                file_path, chunk_content,
                _infer_ts_chunk_type(file_path, chunk_content),
                template_name=template_name,
                metadata={
                    "start_line": import_end + prev_start + 1,
                    "end_line": len(lines),
                    "declaration": last_name,
                },
                suffix=last_name,
            ))

    return chunks if chunks else [_make_chunk(
        file_path, content, _infer_ts_chunk_type(file_path, content),
        template_name=template_name,
    )]


def _find_ts_split_points(content: str) -> List[tuple]:
    """Find line offsets where top-level declarations begin."""
    lines = content.split("\n")
    points: List[tuple] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
            continue

        # Match export declarations
        m = _EXPORT_PATTERN.match(stripped)
        if m:
            # Extract the name
            name = _extract_declaration_name(stripped)
            points.append((i, name or f"export_{i}"))
            continue

        # Match const with cva (shadcn variants)
        m = _CONST_CVA_PATTERN.match(stripped)
        if m:
            points.append((i, m.group(1)))
            continue

        # Match React component definitions (non-export)
        m = _COMPONENT_PATTERN.match(stripped)
        if m and not stripped.startswith("export"):
            points.append((i, m.group(1)))

    return points


def _extract_declaration_name(line: str) -> Optional[str]:
    """Extract the declared name from an export statement."""
    m = re.search(r'(?:function|const|class|interface|type|enum)\s+(\w+)', line)
    return m.group(1) if m else None


def _infer_ts_chunk_type(file_path: str, content: str) -> str:
    """Infer the chunk type based on file path and content patterns."""
    path_lower = file_path.lower()

    if "/ui/" in path_lower or "component" in path_lower:
        return "component"
    if "type" in path_lower or re.search(r'(?:interface|type)\s+\w+', content):
        if "export" in content and ("interface" in content or "type " in content):
            return "type"
    if "action" in path_lower or "'use server'" in content:
        return "function"
    if "layout" in path_lower or "page" in path_lower:
        return "component"
    if "lib/" in path_lower or "utils" in path_lower:
        return "function"
    if "theme" in path_lower or "provider" in path_lower:
        return "component"

    return "function"


def _chunk_json(file_path: str, content: str, template_name: str) -> List[TemplateChunk]:
    """
    Chunk a JSON file by top-level keys.
    Each key becomes a separate chunk for targeted retrieval.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [_make_chunk(file_path, content, "config", template_name=template_name)]

    if not isinstance(data, dict):
        return [_make_chunk(file_path, content, "config", template_name=template_name)]

    chunks: List[TemplateChunk] = []
    for key, value in data.items():
        chunk_content = json.dumps({key: value}, indent=2)
        chunks.append(_make_chunk(
            file_path, chunk_content, "config",
            template_name=template_name,
            metadata={"json_key": key},
            suffix=key,
        ))

    return chunks if chunks else [_make_chunk(file_path, content, "config", template_name=template_name)]


def _chunk_css(file_path: str, content: str, template_name: str) -> List[TemplateChunk]:
    """
    Chunk a CSS file by comment-delimited sections or layer blocks.
    Falls back to single chunk for small files.
    """
    lines = content.split("\n")
    if len(lines) < 30:
        return [_make_chunk(file_path, content, "style", template_name=template_name)]

    # Split on @layer or comment headers like /* === Section === */
    sections: List[tuple] = []
    current_start = 0
    current_name = "base"

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Detect @layer declarations
        layer_match = re.match(r'@layer\s+(\w+)', stripped)
        if layer_match:
            if i > current_start:
                sections.append((current_name, "\n".join(lines[current_start:i]).strip()))
            current_start = i
            current_name = f"layer_{layer_match.group(1)}"
            continue
        # Detect comment headers
        if stripped.startswith("/*") and ("===" in stripped or "---" in stripped):
            if i > current_start:
                sections.append((current_name, "\n".join(lines[current_start:i]).strip()))
            current_start = i
            current_name = re.sub(r'[/\*= \-]', '', stripped).strip()[:30] or f"section_{i}"

    # Last section
    remaining = "\n".join(lines[current_start:]).strip()
    if remaining:
        sections.append((current_name, remaining))

    chunks = []
    for name, section_content in sections:
        if section_content:
            chunks.append(_make_chunk(
                file_path, section_content, "style",
                template_name=template_name,
                metadata={"section": name},
                suffix=name,
            ))

    return chunks if chunks else [_make_chunk(file_path, content, "style", template_name=template_name)]


def _make_chunk(
    file_path: str,
    content: str,
    chunk_type: str,
    template_name: str = "nextjs-app",
    metadata: Optional[Dict[str, Any]] = None,
    suffix: Optional[str] = None,
) -> TemplateChunk:
    """Helper to create a TemplateChunk with a unique ID."""
    chunk_id = f"{template_name}:{file_path}"
    if suffix:
        chunk_id += f":{suffix}"

    return TemplateChunk(
        chunk_id=chunk_id,
        file_path=file_path,
        content=content,
        chunk_type=chunk_type,
        metadata=metadata or {},
        template_name=template_name,
    )
