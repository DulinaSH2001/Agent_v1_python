"""Tests for template chunking logic."""

import pytest
from agent.template_chunker import (
    TemplateChunk,
    chunk_template,
    _chunk_typescript,
    _chunk_json,
    _chunk_css,
    _make_chunk,
)


class TestChunkTypescript:
    """Test TypeScript/TSX file chunking."""

    def test_small_file_single_chunk(self):
        """Files under 40 lines should produce a single chunk."""
        content = '''import { cn } from "@/lib/utils"

export function hello() {
    return "world"
}
'''
        chunks = _chunk_typescript("lib/utils.ts", content, "nextjs-app")
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "function"

    def test_component_file_splits_on_exports(self):
        """Large TSX files should split on export boundaries."""
        content = '''import * as React from "react"
import { cn } from "@/lib/utils"

''' + '\n'.join([f'// line {i}' for i in range(30)]) + '''

export interface ButtonProps {
    variant?: string
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
    ({ className, ...props }, ref) => {
        return <button ref={ref} className={className} {...props} />
    }
)
Button.displayName = "Button"

export { Button }
'''
        chunks = _chunk_typescript("components/ui/button.tsx", content, "nextjs-app")
        assert len(chunks) >= 2  # at least imports + body or split exports

    def test_chunk_has_metadata(self):
        """Chunks should have correct metadata fields."""
        content = "export function test() { return 1; }\n"
        chunks = _chunk_typescript("lib/test.ts", content, "nextjs-app")
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.file_path == "lib/test.ts"
        assert chunk.template_name == "nextjs-app"
        assert chunk.chunk_id.startswith("nextjs-app:lib/test.ts")


class TestChunkJson:
    """Test JSON file chunking."""

    def test_splits_by_top_level_keys(self):
        """JSON files should split into one chunk per top-level key."""
        content = '{"dependencies": {"react": "^19"}, "scripts": {"dev": "next dev"}}'
        chunks = _chunk_json("package.json", content, "nextjs-app")
        assert len(chunks) == 2
        keys = [c.metadata.get("json_key") for c in chunks]
        assert "dependencies" in keys
        assert "scripts" in keys

    def test_invalid_json_single_chunk(self):
        """Invalid JSON should produce a single chunk."""
        content = "not valid json {"
        chunks = _chunk_json("config.json", content, "nextjs-app")
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "config"

    def test_non_dict_json_single_chunk(self):
        """JSON arrays should produce a single chunk."""
        content = '["a", "b", "c"]'
        chunks = _chunk_json("data.json", content, "nextjs-app")
        assert len(chunks) == 1


class TestChunkCss:
    """Test CSS file chunking."""

    def test_small_css_single_chunk(self):
        """Small CSS files should produce a single chunk."""
        content = "body { margin: 0; }\n"
        chunks = _chunk_css("styles/globals.css", content, "nextjs-app")
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "style"

    def test_layer_split(self):
        """CSS with @layer should split on layer boundaries."""
        content = "\n".join([
            "@layer base {",
            "  body { margin: 0; }",
            "}",
            "",
        ] + [f"/* line {i} */" for i in range(30)] + [
            "@layer components {",
            "  .btn { padding: 4px; }",
            "}",
        ])
        chunks = _chunk_css("styles/globals.css", content, "nextjs-app")
        assert len(chunks) >= 2


class TestChunkTemplate:
    """Test full template chunking."""

    def test_chunk_template_produces_chunks(self):
        """chunk_template should produce non-empty results for the nextjs-app template."""
        chunks = chunk_template("nextjs-app")
        assert len(chunks) > 0

    def test_all_chunks_have_required_fields(self):
        """Every chunk should have all required fields populated."""
        chunks = chunk_template("nextjs-app")
        for chunk in chunks:
            assert chunk.chunk_id
            assert chunk.file_path
            assert chunk.content
            assert chunk.chunk_type
            assert chunk.template_name == "nextjs-app"

    def test_no_node_modules_chunks(self):
        """Chunks should not include files from node_modules."""
        chunks = chunk_template("nextjs-app")
        for chunk in chunks:
            assert "node_modules" not in chunk.file_path

    def test_nonexistent_template_empty(self):
        """Non-existent template should produce empty list."""
        chunks = chunk_template("nonexistent-template")
        assert chunks == []


class TestMakeChunk:
    """Test the chunk factory helper."""

    def test_chunk_id_format(self):
        chunk = _make_chunk("app/page.tsx", "content", "component", suffix="MyComponent")
        assert chunk.chunk_id == "nextjs-app:app/page.tsx:MyComponent"

    def test_chunk_id_without_suffix(self):
        chunk = _make_chunk("app/page.tsx", "content", "component")
        assert chunk.chunk_id == "nextjs-app:app/page.tsx"

    def test_to_dict(self):
        chunk = _make_chunk("f.ts", "code", "function", metadata={"key": "val"})
        d = chunk.to_dict()
        assert d["file_path"] == "f.ts"
        assert d["content"] == "code"
        assert d["metadata"]["key"] == "val"
