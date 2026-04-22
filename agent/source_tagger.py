"""Stamp `data-edit-id` attributes on JSX/TSX files.

The visual editor reads this attribute from clicked DOM nodes to map back to
source files. The LLM is asked to produce these attributes (see BUILDER_PROMPT),
but model output drifts, so we re-stamp every generated/modified file as a
post-generation pass.

Strategy: regex-based, deliberately conservative.
- Find each top-level component function (function decl, arrow fn, forwardRef).
- Locate its first JSX opening tag (non-self-closing or self-closing).
- If that tag already has a `data-edit-id`, leave it alone. Otherwise insert one
  keyed to `<file>:<line>:<Component>`.

We only stamp the ROOT JSX element per component, not every nested tag. That is
enough for the visual editor fast-path (Fix 5): the clicked element walks up to
find the nearest `data-edit-id` ancestor, which gives us the owning file.
Stamping every tag would bloat the DOM and churn diffs on every rebuild.
"""

from __future__ import annotations

import re
from typing import Dict, Tuple

# Matches the start of a component-like declaration. We capture the name so we
# can embed it in the attribute. Three shapes cover ~all real cases:
#   1. `export default function Name(` / `function Name(`
#   2. `const Name = (` arrow function (with optional `: React.FC`, generics, etc.)
#   3. `const Name = forwardRef(` / `const Name = memo(`
_COMPONENT_PATTERNS = [
    re.compile(
        r"(?:export\s+default\s+)?(?:export\s+)?(?:async\s+)?function\s+"
        r"(?P<name>[A-Z][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\s*\("
    ),
    re.compile(
        r"(?:export\s+(?:default\s+)?)?const\s+(?P<name>[A-Z][A-Za-z0-9_]*)\s*"
        r"(?::\s*[^=]+)?\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>"
    ),
    re.compile(
        r"(?:export\s+(?:default\s+)?)?const\s+(?P<name>[A-Z][A-Za-z0-9_]*)\s*"
        r"=\s*(?:forwardRef|memo|observer)\s*(?:<[^>]*>)?\s*\("
    ),
]

# Matches a JSX opening tag. Capture the tag name and the char index of the `<`.
# Intentionally ignores fragments (`<>`) and component/html distinction — we
# just need the first tag-opener after the component declaration.
_JSX_OPEN = re.compile(r"<([A-Za-z][A-Za-z0-9._]*)(\s|>|/)")

_DATA_EDIT_ID_PRESENT = re.compile(r"\sdata-edit-id\s*=")


def _find_components(source: str):
    """Yield (name, start_offset) for each component declaration, in source order."""
    hits = []
    for pat in _COMPONENT_PATTERNS:
        for m in pat.finditer(source):
            hits.append((m.start(), m.group("name")))
    hits.sort(key=lambda x: x[0])
    # Dedupe by offset — a declaration may match multiple patterns in edge cases
    seen = set()
    for offset, name in hits:
        if offset in seen:
            continue
        seen.add(offset)
        yield name, offset


def _find_root_jsx(source: str, start: int) -> Tuple[int, int, str] | None:
    """Find the first JSX opening tag after `start`. Returns (tag_start, insert_pos, tag_name)."""
    m = _JSX_OPEN.search(source, start)
    if not m:
        return None
    tag_start = m.start()
    tag_name = m.group(1)
    # Insert the attribute right after the tag name, before any existing attributes.
    # `insert_pos` is the index AFTER the tag name (and before the whitespace or `>`).
    insert_pos = m.start() + 1 + len(tag_name)
    return tag_start, insert_pos, tag_name


def _line_of(source: str, offset: int) -> int:
    """1-indexed line number of the byte offset."""
    return source.count("\n", 0, offset) + 1


def stamp_file(relative_path: str, source: str) -> str:
    """Return `source` with `data-edit-id` attributes added to component root elements.

    Idempotent: tags that already have `data-edit-id` are left untouched.
    """
    if not source or not relative_path:
        return source
    if not relative_path.endswith((".tsx", ".jsx")):
        # .ts/.js files can contain JSX but rarely host React components in this codebase
        return source

    # Build insertions from END to START so offsets stay valid as we splice.
    insertions: list[tuple[int, str]] = []

    for name, decl_offset in _find_components(source):
        jsx = _find_root_jsx(source, decl_offset)
        if not jsx:
            continue
        tag_start, insert_pos, _tag_name = jsx

        # Check the tag's attribute region for an existing data-edit-id.
        # Look ahead up to the matching `>` (we don't parse JSX properly; cap at 500 chars).
        tail = source[tag_start:tag_start + 500]
        close_idx = tail.find(">")
        attr_region = tail[: close_idx if close_idx != -1 else len(tail)]
        if _DATA_EDIT_ID_PRESENT.search(attr_region):
            continue

        line = _line_of(source, tag_start)
        attr = f' data-edit-id="{relative_path}:{line}:{name}"'
        insertions.append((insert_pos, attr))

    if not insertions:
        return source

    insertions.sort(key=lambda x: x[0], reverse=True)
    buf = source
    for pos, attr in insertions:
        buf = buf[:pos] + attr + buf[pos:]
    return buf


def stamp_file_system(file_system: Dict[str, str]) -> Dict[str, str]:
    """Apply `stamp_file` to every `.tsx`/`.jsx` entry in a file map."""
    stamped = dict(file_system)
    for path, content in file_system.items():
        if isinstance(content, str):
            stamped[path] = stamp_file(path, content)
    return stamped
