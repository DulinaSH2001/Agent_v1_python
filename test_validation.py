"""
Test Suite for Validation Layer

Tests for the enhanced validation system including:
- TypeScript/TSX validation
- React 19 pattern validation
- Next.js 16 pattern validation
- Security validation
- Dependency validation
"""

import pytest
from agent.code_validator import (
    ValidationResult,
    validate_typescript_advanced,
    check_react_patterns,
    check_nextjs_patterns,
    validate_security,
    validate_dependencies,
)
from agent.execution_layer import clean_generated_code


class TestTypeScriptValidation:
    """Test TypeScript/TSX validation."""

    def test_valid_server_component(self):
        """Valid async Server Component should pass."""
        code = """
export default async function Page() {
  const data = await fetch('/api/data');
  return <div>{data.title}</div>;
}
"""
        result = validate_typescript_advanced(code, "app/page.tsx")
        assert result.is_valid
        assert len(result.errors) == 0

    def test_invalid_client_component_async(self):
        """Client Component cannot be async."""
        code = """
'use client'

export default async function Page() {
  const data = await fetch('/api/data');
  return <div>{data.title}</div>;
}
"""
        result = validate_typescript_advanced(code, "app/page.tsx")
        assert not result.is_valid
        assert any("cannot be async" in str(e).lower() for e in result.errors)

    def test_missing_page_export(self):
        """page.tsx must have default export."""
        code = """
function Page() {
  return <div>Hello</div>;
}
"""
        result = validate_typescript_advanced(code, "app/page.tsx")
        assert not result.is_valid
        assert any("export" in str(e).lower() for e in result.errors)

    def test_route_handler_validation(self):
        """Route handlers must export HTTP methods."""
        code = """
function handler() {
  return Response.json({ data: [] });
}
"""
        result = validate_typescript_advanced(code, "app/api/users/route.ts")
        assert not result.is_valid
        assert any("http method" in str(e).lower() for e in result.errors)


class TestReactPatterns:
    """Test React 19 pattern validation."""

    def test_use_memo_warning(self):
        """Should warn about useMemo (React Compiler handles it)."""
        code = """
'use client'

import { useMemo } from 'react';

export function Component() {
  const value = useMemo(() => expensive(), []);
  return <div>{value}</div>;
}
"""
        issues = check_react_patterns(code, "components/test.tsx")
        warnings = [i for i in issues if i.severity == "WARNING"]
        assert len(warnings) > 0
        assert any("usememo" in w.message.lower() for w in warnings)

    def test_client_hooks_without_directive(self):
        """Component using hooks should have 'use client'."""
        code = """
import { useState } from 'react';

export function Component() {
  const [count, setCount] = useState(0);
  return <button onClick={() => setCount(count + 1)}>{count}</button>;
}
"""
        issues = check_react_patterns(code, "components/counter.tsx")
        warnings = [i for i in issues if i.severity == "WARNING"]
        assert any("use client" in w.message.lower() for w in warnings)


class TestNextJSPatterns:
    """Test Next.js 16 pattern validation."""

    def test_server_action_without_async(self):
        """Server Actions must be async."""
        code = """
'use server'

function updateUser(data) {
  // ...
}
"""
        issues = check_nextjs_patterns(code, "lib/actions/users.ts")
        errors = [i for i in issues if i.severity == "ERROR"]
        assert any("async" in e.message.lower() for e in errors)

    def test_server_action_revalidate_import(self):
        """revalidatePath must be imported from next/cache."""
        code = """
'use server'

async function updateUser(data) {
  revalidatePath('/users');
}
"""
        issues = check_nextjs_patterns(code, "lib/actions/users.ts")
        errors = [i for i in issues if i.severity == "ERROR"]
        assert any("next/cache" in e.message.lower() for e in errors)

    def test_server_action_validation_warning(self):
        """Server Actions should validate inputs."""
        code = """
'use server'

async function updateUser(data: any) {
  await db.user.update({ data });
}
"""
        issues = check_nextjs_patterns(code, "lib/actions/users.ts")
        warnings = [i for i in issues if i.severity == "WARNING"]
        assert any("zod" in w.message.lower()
                   or "validate" in w.message.lower() for w in warnings)


class TestSecurityValidation:
    """Test security vulnerability detection."""

    def test_dangerous_set_inner_html(self):
        """Should detect dangerouslySetInnerHTML."""
        code = """
export function Component({ html }) {
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}
"""
        issues = validate_security(code, "components/test.tsx")
        errors = [i for i in issues if i.severity == "ERROR"]
        assert len(errors) > 0
        assert any("xss" in e.message.lower() for e in errors)

    def test_hardcoded_api_key(self):
        """Should detect hardcoded secrets."""
        code = """
const API_KEY = "sk-1234567890abcdef1234567890abcdef";

async function callAPI() {
  const response = await fetch('/api', {
    headers: { 'Authorization': `Bearer ${API_KEY}` }
  });
}
"""
        issues = validate_security(code, "lib/api.ts")
        errors = [i for i in issues if i.severity == "ERROR"]
        assert any("hardcoded" in e.message.lower() for e in errors)

    def test_eval_usage(self):
        """Should detect eval() usage."""
        code = """
function dangerousFunction(code) {
  return eval(code);
}
"""
        issues = validate_security(code, "lib/utils.ts")
        errors = [i for i in issues if i.severity == "ERROR"]
        assert any("eval" in e.message.lower() for e in errors)


class TestDependencyValidation:
    """Test dependency validation."""

    def test_missing_dependency(self):
        """Should detect missing dependencies."""
        file_system = {
            "package.json": '{"dependencies": {}}',
            "app/page.tsx": "import { Button } from 'some-ui-lib';"
        }

        report = validate_dependencies(file_system)
        assert len(report.missing_dependencies) > 0
        assert any("some-ui-lib" in dep for dep in report.missing_dependencies)

    def test_unresolved_internal_import(self):
        """Should detect unresolved internal imports."""
        file_system = {
            "package.json": '{}',
            "app/page.tsx": "import { Button } from '@/components/ui/button';"
        }

        report = validate_dependencies(file_system)
        assert len(report.unresolved_imports) > 0

    def test_shadcn_without_config(self):
        """Should warn if using shadcn without components.json."""
        file_system = {
            "package.json": '{}',
            "app/page.tsx": "import { Button } from '@/components/ui/button';"
        }

        report = validate_dependencies(file_system)
        assert len(report.shadcn_issues) > 0


class TestCodeCleaning:
    """Test code cleaning utilities."""

    def test_remove_markdown_blocks(self):
        """Should remove markdown code blocks."""
        code = '''```typescript
export function Component() {
  return <div>Hello</div>;
}
```'''
        cleaned = clean_generated_code(code)
        assert not cleaned.startswith("```")
        assert "export function Component" in cleaned

    def test_remove_placeholder_comments(self):
        """Should remove placeholder comments."""
        code = """
function Component() {
  // ... existing code ...
  return <div>Hello</div>;
  // ... rest of the code ...
}
"""
        cleaned = clean_generated_code(code)
        assert "... existing code ..." not in cleaned
        assert "... rest of the code ..." not in cleaned

    def test_normalize_line_endings(self):
        """Should normalize line endings."""
        code = "function test() {\r\n  return true;\r\n}"
        cleaned = clean_generated_code(code)
        assert "\r\n" not in cleaned
        assert "\n" in cleaned


class TestGuardRails:
    """Test generation guard rails."""

    def test_file_size_limit(self):
        """Should detect oversized files."""
        from agent.execution_layer import GenerationGuardRails

        large_code = "// " + ("x" * 10000)  # > 8KB
        error = GenerationGuardRails.check_file_size("test.ts", large_code)
        assert error is not None
        assert "too large" in error.lower()

    def test_nesting_depth_limit(self):
        """Should detect excessive nesting."""
        from agent.execution_layer import GenerationGuardRails

        nested_code = """
function level1() {
  if (true) {
    if (true) {
      if (true) {
        if (true) {
          if (true) {
            if (true) {
              if (true) {
                // Too deep!
              }
            }
          }
        }
      }
    }
  }
}
"""
        error = GenerationGuardRails.check_nesting_depth(nested_code)
        assert error is not None
        assert "nesting" in error.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
