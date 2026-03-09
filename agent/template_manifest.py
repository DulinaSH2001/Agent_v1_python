"""
Antigravity Agent - Template Manifest

Generates a compact, structured summary of the entire template so the agent
has complete knowledge of available files, components, types, and dependencies
BEFORE planning. This is injected into user content (not system prompt) to
avoid Azure content filter issues.

The manifest provides breadth (knows everything); RAG provides depth
(code examples for query-relevant components).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Template root
_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "nextjs-app"

# Cached manifest (built once per process)
_manifest_cache: Optional[Dict[str, Any]] = None
_manifest_text_cache: Optional[str] = None
_manifest_short_cache: Optional[str] = None


def _extract_exports(content: str) -> List[str]:
    """Extract exported names from TypeScript/TSX content (outside JSDoc/comments)."""
    # Strip JSDoc blocks and comments to avoid picking up example code
    stripped = re.sub(r"/\*\*[\s\S]*?\*/", "", content)
    stripped = re.sub(r"//.*$", "", stripped, flags=re.MULTILINE)

    exports = []
    # export default function/class Name
    for m in re.finditer(r"export\s+default\s+(?:function|class)\s+(\w+)", stripped):
        exports.append(m.group(1))
    # export function/const/class Name (skip interface/type — those are types not components)
    for m in re.finditer(r"export\s+(?:function|const|class)\s+(\w+)", stripped):
        exports.append(m.group(1))
    return list(dict.fromkeys(exports))  # dedupe preserving order


def _extract_props_interface(content: str, component_name: str) -> str:
    """Extract the props interface for a component."""
    # Look for interface {ComponentName}Props or {ComponentName}Props<T>
    pattern = rf"interface\s+{component_name}Props(?:<[^>]+>)?\s*\{{([^}}]*)\}}"
    m = re.search(pattern, content, re.DOTALL)
    if m:
        # Extract just the prop names and types, one line each
        body = m.group(1)
        props = []
        for line in body.strip().split("\n"):
            line = line.strip().rstrip(";").strip()
            if line and not line.startswith("/*") and not line.startswith("*") and not line.startswith("//"):
                props.append(line)
        return "; ".join(props)
    return ""


def _extract_component_tag(content: str) -> str:
    """Extract @component tag value from JSDoc."""
    m = re.search(r"@component\s+(\w+)", content)
    return m.group(1) if m else ""


def build_template_manifest() -> Dict[str, Any]:
    """Scan the template directory and build a structured manifest."""
    global _manifest_cache
    if _manifest_cache is not None:
        return _manifest_cache

    manifest: Dict[str, Any] = {
        "dependencies": {},
        "dev_dependencies": {},
        "shadcn_components": [],
        "custom_components": {},
        "types": {},
        "utilities": {},
        "base_files": [],
        "styles": [],
        "config_files": [],
    }

    if not _TEMPLATE_DIR.exists():
        logger.warning(f"Template directory not found: {_TEMPLATE_DIR}")
        _manifest_cache = manifest
        return manifest

    # Parse package.json
    pkg_path = _TEMPLATE_DIR / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text())
            manifest["dependencies"] = pkg.get("dependencies", {})
            manifest["dev_dependencies"] = pkg.get("devDependencies", {})
        except Exception as e:
            logger.warning(f"Failed to parse package.json: {e}")

    # Scan UI components (Shadcn)
    ui_dir = _TEMPLATE_DIR / "components" / "ui"
    if ui_dir.exists():
        manifest["shadcn_components"] = sorted(
            f.stem for f in ui_dir.glob("*.tsx")
        )

    # Scan custom components (layout + data)
    for subdir in ["layout", "data"]:
        comp_dir = _TEMPLATE_DIR / "components" / subdir
        if not comp_dir.exists():
            continue
        for f in comp_dir.glob("*.tsx"):
            rel_path = f"components/{subdir}/{f.name}"
            content = f.read_text()
            component_name = _extract_component_tag(content) or f.stem
            props = _extract_props_interface(content, component_name)
            exports = _extract_exports(content)
            manifest["custom_components"][rel_path] = {
                "name": component_name,
                "exports": exports,
                "props": props,
                "import_path": f"@/components/{subdir}/{f.stem}",
            }

    # Scan types (include interfaces and type aliases)
    types_dir = _TEMPLATE_DIR / "types"
    if types_dir.exists():
        for f in types_dir.glob("*.ts"):
            rel_path = f"types/{f.name}"
            content = f.read_text()
            type_names = []
            for m in re.finditer(
                r"export\s+(?:interface|type)\s+(\w+)", content
            ):
                type_names.append(m.group(1))
            manifest["types"][rel_path] = list(dict.fromkeys(type_names))

    # Scan utilities
    lib_dir = _TEMPLATE_DIR / "lib"
    if lib_dir.exists():
        for f in lib_dir.glob("*.ts"):
            rel_path = f"lib/{f.name}"
            content = f.read_text()
            exports = _extract_exports(content)
            manifest["utilities"][rel_path] = exports

    # Base app files
    app_dir = _TEMPLATE_DIR / "app"
    if app_dir.exists():
        manifest["base_files"] = sorted(
            f"app/{f.name}" for f in app_dir.glob("*.tsx")
        )

    # Styles
    styles_dir = _TEMPLATE_DIR / "styles"
    if styles_dir.exists():
        manifest["styles"] = sorted(
            f"styles/{f.name}" for f in styles_dir.iterdir() if f.is_file()
        )

    # Config files
    for cfg in ["next.config.js", "tsconfig.json", "tailwind.config.js", "postcss.config.js", "package.json"]:
        if (_TEMPLATE_DIR / cfg).exists():
            manifest["config_files"].append(cfg)

    _manifest_cache = manifest
    return manifest


def get_manifest_for_prompt() -> str:
    """
    Return a compact text representation of the full template manifest
    for injection into the architect's user content (~40-50 lines).
    """
    global _manifest_text_cache
    if _manifest_text_cache is not None:
        return _manifest_text_cache

    m = build_template_manifest()
    lines = ["## Template Knowledge Base\n"]

    # Dependencies
    dep_names = sorted(m["dependencies"].keys())
    lines.append(f"**Installed packages**: {', '.join(dep_names)}")
    lines.append("")

    # Shadcn components
    lines.append(f"**Shadcn UI components**: {', '.join(m['shadcn_components'])}")
    lines.append("")

    # Custom components
    lines.append("**Pre-built custom components** (import and use directly):")
    for path, info in m["custom_components"].items():
        props_str = f" — Props: {info['props']}" if info["props"] else ""
        lines.append(f"  - `{info['import_path']}` exports: {', '.join(info['exports'])}{props_str}")
    lines.append("")

    # Types
    lines.append("**Shared types** (import from `@/types`):")
    for path, exports in m["types"].items():
        lines.append(f"  - {', '.join(exports)}")
    lines.append("")

    # Utilities
    lines.append("**Utilities**:")
    for path, exports in m["utilities"].items():
        lines.append(f"  - `@/{path.replace('.ts', '')}`: {', '.join(exports)}")
    lines.append("")

    # Base files
    lines.append(f"**Base app files**: {', '.join(m['base_files'])}")
    lines.append(f"**Config files**: {', '.join(m['config_files'])}")
    lines.append("")

    lines.append("You may modify any of these files. For new npm packages, add them to the task `dependencies` array.")

    _manifest_text_cache = "\n".join(lines)
    return _manifest_text_cache


def get_short_manifest_for_builder() -> str:
    """
    Return a shorter version (imports + props only) for the builder prompt.
    """
    global _manifest_short_cache
    if _manifest_short_cache is not None:
        return _manifest_short_cache

    m = build_template_manifest()
    lines = ["## Available Components\n"]

    # One-line usage examples per custom component
    _USAGE_EXAMPLES = {
        "components/layout/Sidebar.tsx": "<Sidebar links={navLinks} />",
        "components/layout/Header.tsx": '<Header breadcrumbs={[{label: "Page"}]} />',
        "components/layout/PageContainer.tsx": "<PageContainer>{children}</PageContainer>",
        "components/layout/Footer.tsx": "<Footer />",
        "components/data/DataTable.tsx": "<DataTable<T> data={items} columns={cols} />",
        "components/data/StatCard.tsx": '<StatCard title="Revenue" value="$1k" change={5} icon={DollarSign} />',
        "components/data/EmptyState.tsx": '<EmptyState title="No items" description="Get started" />',
    }

    # Custom components (compact with usage examples)
    for path, info in m["custom_components"].items():
        props_str = f"({info['props']})" if info["props"] else "()"
        usage = _USAGE_EXAMPLES.get(path, "")
        usage_hint = f"\n  Usage: `{usage}`" if usage else ""
        lines.append(
            f"- `import {{ {', '.join(info['exports'])} }} "
            f"from \"{info['import_path']}\"`  {props_str}{usage_hint}"
        )

    lines.append("")
    lines.append(f"Shadcn: {', '.join(m['shadcn_components'])}")
    lines.append(f"Types from @/types: {', '.join(sum(m['types'].values(), []))}")
    lines.append("Use `sonner` for toasts (not @/components/ui/toast).")

    _manifest_short_cache = "\n".join(lines)
    return _manifest_short_cache


def invalidate_cache():
    """Clear cached manifest (useful for testing)."""
    global _manifest_cache, _manifest_text_cache, _manifest_short_cache
    _manifest_cache = None
    _manifest_text_cache = None
    _manifest_short_cache = None
