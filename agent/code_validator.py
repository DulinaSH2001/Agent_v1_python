"""
Antigravity Agent - Code Validator

Provides validation for generated code before upload to catch syntax errors early.

Enhanced with:
- Advanced TypeScript/TSX validation
- React 19 pattern validation
- Next.js 16 pattern validation
- Security vulnerability detection
- Dependency validation
"""

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes for Validation Results
# =============================================================================

@dataclass
class ValidationIssue:
    """Represents a single validation issue."""
    severity: str  # "ERROR", "WARNING", "INFO"
    category: str  # "SYNTAX", "TYPE", "IMPORT", "SECURITY", "PATTERN"
    message: str
    line: Optional[int] = None
    file_path: Optional[str] = None

    def __str__(self) -> str:
        location = f" (line {self.line})" if self.line else ""
        return f"[{self.severity}] {self.category}: {self.message}{location}"


@dataclass
class ValidationResult:
    """Comprehensive validation result."""
    is_valid: bool
    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)
    info: List[ValidationIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    @property
    def has_critical_issues(self) -> bool:
        """Check for critical security or blocking issues."""
        return any("CRITICAL" in str(e) for e in self.errors)

    def add_error(self, category: str, message: str, line: Optional[int] = None) -> None:
        self.errors.append(ValidationIssue("ERROR", category, message, line))
        self.is_valid = False

    def add_warning(self, category: str, message: str, line: Optional[int] = None) -> None:
        self.warnings.append(ValidationIssue(
            "WARNING", category, message, line))

    def add_info(self, category: str, message: str, line: Optional[int] = None) -> None:
        self.info.append(ValidationIssue("INFO", category, message, line))


@dataclass
class DependencyReport:
    """Report on dependency validation."""
    missing_dependencies: List[str] = field(default_factory=list)
    circular_imports: List[str] = field(default_factory=list)
    unresolved_imports: List[str] = field(default_factory=list)
    shadcn_issues: List[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return (
            len(self.missing_dependencies) > 0 or
            len(self.circular_imports) > 0 or
            len(self.unresolved_imports) > 0 or
            len(self.shadcn_issues) > 0
        )


logger = logging.getLogger(__name__)


def validate_python(code: str) -> Tuple[bool, str]:
    """
    Validate Python code syntax using AST parsing.

    Args:
        code: Python source code to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Parse error: {str(e)}"


def validate_typescript(code: str) -> Tuple[bool, str]:
    """
    Basic TypeScript/JavaScript validation (checks for common syntax errors).

    Note: This is a lightweight check, not a full parser.
    For production, consider using a proper TS/JS parser.

    Args:
        code: TypeScript/JavaScript source code to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check for balanced braces, brackets, parentheses
    checks = [
        ('{', '}', "Unmatched curly braces"),
        ('(', ')', "Unmatched parentheses"),
        ('[', ']', "Unmatched square brackets"),
    ]

    for open_char, close_char, error_msg in checks:
        open_count = code.count(open_char)
        close_count = code.count(close_char)
        if open_count != close_count:
            return False, f"{error_msg} (found {open_count} '{open_char}' and {close_count} '{close_char}')"

    # Check for common syntax errors
    if 'import {' in code and '} from' not in code:
        return False, "Incomplete import statement (missing '} from')"

    # Check for unterminated strings (simple check)
    single_quotes = code.count("'") - code.count("\\'")
    double_quotes = code.count('"') - code.count('\\"')
    backticks = code.count('`') - code.count('\\`')

    if single_quotes % 2 != 0:
        return False, "Unterminated single-quoted string"
    if double_quotes % 2 != 0:
        return False, "Unterminated double-quoted string"
    if backticks % 2 != 0:
        return False, "Unterminated template literal"

    return True, ""


def validate_json(code: str) -> Tuple[bool, str]:
    """
    Validate JSON syntax.

    Args:
        code: JSON content to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        import json
        json.loads(code)
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"JSON error at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Parse error: {str(e)}"


def validate_file(file_path: str, content: str) -> Tuple[bool, str]:
    """
    Validate file content based on its extension.

    Args:
        file_path: Path to the file (used to determine type)
        content: File content to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Python files
    if file_path.endswith('.py'):
        return validate_python(content)

    # TypeScript/JavaScript files
    elif file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
        return validate_typescript(content)

    # JSON files
    elif file_path.endswith('.json'):
        return validate_json(content)

    # Other files - skip validation
    else:
        return True, ""


def get_file_size_mb(content: str) -> float:
    """Get file size in megabytes."""
    return len(content.encode('utf-8')) / (1024 * 1024)


def validate_file_size(file_path: str, content: str, max_size_mb: float = 5.0) -> Tuple[bool, str]:
    """
    Validate that file size is within acceptable limits.
    
    Args:
        file_path: Path to the file
        content: File content
        max_size_mb: Maximum allowed file size in MB
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    size_mb = get_file_size_mb(content)
    
    if size_mb > max_size_mb:
        return False, f"File too large: {size_mb:.2f}MB (max: {max_size_mb}MB)"
    
    return True, ""


# =============================================================================
# Advanced TypeScript/TSX Validation
# =============================================================================

def validate_typescript_advanced(code: str, file_path: str) -> ValidationResult:
    """
    Advanced TypeScript/TSX validation.
    
    Checks:
    1. Import/export validity
    2. JSX syntax correctness
    3. React patterns
    4. Next.js specific patterns
    5. Common anti-patterns
    
    Args:
        code: TypeScript/TSX code to validate
        file_path: Path to the file being validated
        
    Returns:
        ValidationResult with detailed errors and warnings
    """
    result = ValidationResult(is_valid=True)
    
    # First check basic syntax
    is_valid, error = validate_typescript(code)
    if not is_valid:
        result.add_error("SYNTAX", error)
        return result
    
    # Check imports
    import_issues = check_imports(code, file_path)
    for issue in import_issues:
        if issue.severity == "ERROR":
            result.errors.append(issue)
            result.is_valid = False
        else:
            result.warnings.append(issue)
    
    # Check exports (for Next.js pages, layouts, etc.)
    export_issues = check_exports(code, file_path)
    for issue in export_issues:
        if issue.severity == "ERROR":
            result.errors.append(issue)
            result.is_valid = False
        else:
            result.warnings.append(issue)
    
    # Check React 19 patterns
    react_issues = check_react_patterns(code, file_path)
    for issue in react_issues:
        if issue.severity == "ERROR":
            result.errors.append(issue)
            result.is_valid = False
        else:
            result.warnings.append(issue)
    
    # Check Next.js 16 patterns
    nextjs_issues = check_nextjs_patterns(code, file_path)
    for issue in nextjs_issues:
        if issue.severity == "ERROR":
            result.errors.append(issue)
            result.is_valid = False
        else:
            result.warnings.append(issue)
    
    return result


def check_imports(code: str, file_path: str) -> List[ValidationIssue]:
    """
    Validate import statements.
    
    Checks:
    - Proper import syntax
    - shadcn/ui imports from correct path
    - Next.js imports
    """
    issues = []
    lines = code.split('\n')
    
    for i, line in enumerate(lines, 1):
        line_stripped = line.strip()
        
        # Check for incomplete imports
        if 'import {' in line_stripped and '} from' not in line_stripped:
            # Check if it continues on next line
            if i < len(lines) and '} from' not in lines[i]:
                issues.append(ValidationIssue(
                    "ERROR", "IMPORT",
                    "Incomplete import statement (missing '} from')",
                    line=i
                ))
        
        # Check shadcn component imports
        if '@/components/ui/' in line_stripped:
            # Valid shadcn import path
            pass
        elif 'from "shadcn' in line_stripped or "from 'shadcn" in line_stripped:
            issues.append(ValidationIssue(
                "ERROR", "IMPORT",
                "Invalid shadcn import - use '@/components/ui/*' instead",
                line=i
            ))
        
        # Check for default Next.js imports
        if 'import Image from' in line_stripped:
            if 'next/image' not in line_stripped:
                issues.append(ValidationIssue(
                    "WARNING", "IMPORT",
                    "Image should be imported from 'next/image'",
                    line=i
                ))
        
        if 'import Link from' in line_stripped:
            if 'next/link' not in line_stripped:
                issues.append(ValidationIssue(
                    "WARNING", "IMPORT",
                    "Link should be imported from 'next/link'",
                    line=i
                ))
    
    return issues


def check_exports(code: str, file_path: str) -> List[ValidationIssue]:
    """
    Validate export statements for Next.js files.
    
    Checks:
    - page.tsx must export default function
    - layout.tsx must export default function
    - Route handlers export correct methods
    """
    issues = []
    
    # Check if it's a Next.js page
    if 'page.tsx' in file_path or 'page.ts' in file_path:
        if 'export default' not in code:
            issues.append(ValidationIssue(
                "ERROR", "EXPORT",
                "page.tsx must export a default component"
            ))
        
        # Check for proper function export
        has_function_export = (
            'export default function' in code or
            'export default async function' in code or
            'export { default }' in code or
            'export default' in code  # Could be arrow function
        )
        
        if not has_function_export:
            issues.append(ValidationIssue(
                "WARNING", "EXPORT",
                "page.tsx should export a function component"
            ))
    
    # Check if it's a layout
    if 'layout.tsx' in file_path or 'layout.ts' in file_path:
        if 'export default' not in code:
            issues.append(ValidationIssue(
                "ERROR", "EXPORT",
                "layout.tsx must export a default component"
            ))
    
    # Check if it's a route handler
    if '/route.ts' in file_path or '/route.tsx' in file_path:
        http_methods = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']
        has_method_export = any(f'export async function {method}' in code for method in http_methods)
        
        if not has_method_export:
            issues.append(ValidationIssue(
                "ERROR", "EXPORT",
                "Route handlers must export at least one HTTP method (GET, POST, etc.)"
            ))
    
    return issues


def check_react_patterns(code: str, file_path: str) -> List[ValidationIssue]:
    """
    Validate React 19 patterns and best practices.
    
    Checks:
    - Avoid useMemo/useCallback (React Compiler handles it)
    - Proper use of 'use client' directive
    - Server Component async patterns
    """
    issues = []
    
    has_use_client = "'use client'" in code or '"use client"' in code
    has_use_server = "'use server'" in code or '"use server"' in code
    
    # Check: Avoid useMemo/useCallback in most cases
    if 'useMemo' in code or 'useCallback' in code:
        issues.append(ValidationIssue(
            "WARNING", "PATTERN",
            "React 19 Compiler optimizes automatically - useMemo/useCallback usually unnecessary"
        ))
    
    # Check: Server Component with async
    if 'export default async function' in code:
        if has_use_client:
            issues.append(ValidationIssue(
                "ERROR", "PATTERN",
                "Client Components cannot be async - remove 'use client' or make component sync"
            ))
    
    # Check: Client Component with hooks
    react_hooks = ['useState', 'useEffect', 'useReducer', 'useContext', 'useRef']
    uses_hooks = any(hook in code for hook in react_hooks)
    
    if uses_hooks and not has_use_client and not has_use_server:
        # Check if it's a page or component that might need 'use client'
        if file_path.endswith(('.tsx', '.jsx')):
            issues.append(ValidationIssue(
                "WARNING", "PATTERN",
                f"Component uses React hooks but missing 'use client' directive"
            ))
    
    # Check: use() hook (React 19 Suspense)
    if 'use(' in code and has_use_client:
        # Should have Suspense boundary
        if 'Suspense' not in code:
            issues.append(ValidationIssue(
                "WARNING", "PATTERN",
                "use() hook requires Suspense boundary wrapper"
            ))
    
    return issues


def check_nextjs_patterns(code: str, file_path: str) -> List[ValidationIssue]:
    """
    Validate Next.js 16 patterns and best practices.
    
    Checks:
    - Server Actions patterns
    - Metadata API usage
    - Route handlers
    - Dynamic routes
    """
    issues = []
    
    has_use_server = "'use server'" in code or '"use server"' in code
    
    # Check: Server Actions
    if has_use_server:
        # Must be async function
        if 'async function' not in code:
            issues.append(ValidationIssue(
                "ERROR", "PATTERN",
                "Server Actions must be async functions"
            ))
        
        # Check for revalidatePath/revalidateTag imports
        if 'revalidatePath' in code or 'revalidateTag' in code:
            if 'from "next/cache"' not in code and "from 'next/cache'" not in code:
                issues.append(ValidationIssue(
                    "ERROR", "IMPORT",
                    "revalidatePath/revalidateTag must be imported from 'next/cache'"
                ))
        
        # Check for redirect import
        if 'redirect(' in code:
            if 'from "next/navigation"' not in code and "from 'next/navigation'" not in code:
                issues.append(ValidationIssue(
                    "ERROR", "IMPORT",
                    "redirect must be imported from 'next/navigation'"
                ))
        
        # Recommend input validation
        if 'zod' not in code.lower() and 'z.' not in code:
            issues.append(ValidationIssue(
                "WARNING", "PATTERN",
                "Server Actions should validate inputs with Zod for type safety"
            ))
    
    # Check: Metadata API (for pages)
    if 'page.tsx' in file_path:
        if 'export const metadata' in code or 'export async function generateMetadata' in code:
            # Valid metadata export
            if 'from "next"' not in code and "from 'next'" not in code:
                issues.append(ValidationIssue(
                    "INFO", "PATTERN",
                    "Metadata API detected - ensure Metadata type is imported from 'next'"
                ))
    
    # Check: Dynamic routes usage
    if '[' in file_path and ']' in file_path:
        # This is a dynamic route - check for params usage
        if 'params' not in code:
            issues.append(ValidationIssue(
                "WARNING", "PATTERN",
                "Dynamic route should use params prop"
            ))
    
    return issues


# =============================================================================
# Security Validation
# =============================================================================

def validate_security(code: str, file_path: str) -> List[ValidationIssue]:
    """
    Check for common security vulnerabilities.
    
    Checks:
    1. XSS vulnerabilities (dangerouslySetInnerHTML)
    2. Hardcoded secrets
    3. SQL injection patterns
    4. Missing input validation
    """
    issues = []
    
    # Check: dangerouslySetInnerHTML usage
    if 'dangerouslySetInnerHTML' in code:
        issues.append(ValidationIssue(
            "ERROR", "SECURITY",
            "CRITICAL: Avoid dangerouslySetInnerHTML - XSS vulnerability. Use DOMPurify if necessary"
        ))
    
    # Check: Hardcoded secrets
    secret_patterns = [
        (r'api[_-]?key\s*[=:]\s*["\'][^"\']{20,}["\']', "API key"),
        (r'password\s*[=:]\s*["\'][^"\']+["\']', "Password"),
        (r'secret\s*[=:]\s*["\'][^"\']{20,}["\']', "Secret"),
        (r'token\s*[=:]\s*["\'][^"\']{20,}["\']', "Token"),
    ]
    
    for pattern, secret_type in secret_patterns:
        if re.search(pattern, code, re.IGNORECASE):
            issues.append(ValidationIssue(
                "ERROR", "SECURITY",
                f"CRITICAL: Hardcoded {secret_type} detected - use environment variables (process.env)"
            ))
    
    # Check: SQL injection (if using raw SQL)
    if re.search(r'(SELECT|INSERT|UPDATE|DELETE).+\$\{', code, re.IGNORECASE):
        issues.append(ValidationIssue(
            "ERROR", "SECURITY",
            "Potential SQL injection - use parameterized queries or ORM"
        ))
    
    # Check: eval() usage
    if 'eval(' in code:
        issues.append(ValidationIssue(
            "ERROR", "SECURITY",
            "CRITICAL: eval() usage detected - major security risk"
        ))
    
    # Check: Server Actions without validation
    has_use_server = "'use server'" in code or '"use server"' in code
    if has_use_server:
        if 'zod' not in code.lower() and 'z.' not in code and 'schema' not in code.lower():
            issues.append(ValidationIssue(
                "WARNING", "SECURITY",
                "Server Action missing input validation - validate with Zod schemas"
            ))
    
    return issues


# =============================================================================
# Dependency Validation
# =============================================================================

def extract_imports(code: str) -> List[str]:
    """Extract all import statements from code."""
    imports = []
    lines = code.split('\n')
    
    for line in lines:
        line_stripped = line.strip()
        
        # Match: import ... from '...'
        match = re.search(r'from\s+["\']([^"\']+)["\']', line_stripped)
        if match:
            imports.append(match.group(1))
        
        # Match: import('...')
        match = re.search(r'import\(["\']([^"\']+)["\']\)', line_stripped)
        if match:
            imports.append(match.group(1))
    
    return imports


def resolve_internal_import(import_path: str, current_file: str, file_system: Dict[str, str]) -> bool:
    """Check if an internal import (@/ or ./) can be resolved."""
    if import_path.startswith('@/'):
        # Remove @/ prefix
        resolved_path = import_path[2:]
        
        # Try with common extensions
        for ext in ['.ts', '.tsx', '.js', '.jsx', '']:
            test_path = resolved_path + ext
            if test_path in file_system:
                return True
            # Also check index files
            test_path = resolved_path + '/index' + ext
            if test_path in file_system:
                return True
    
    elif import_path.startswith('.'):
        # Relative import - resolve relative to current file
        current_dir = '/'.join(current_file.split('/')[:-1])
        resolved = current_dir + '/' + import_path
        
        # Normalize path
        parts = resolved.split('/')
        normalized = []
        for part in parts:
            if part == '..':
                if normalized:
                    normalized.pop()
            elif part != '.':
                normalized.append(part)
        
        resolved_path = '/'.join(normalized)
        
        # Try with extensions
        for ext in ['.ts', '.tsx', '.js', '.jsx', '']:
            test_path = resolved_path + ext
            if test_path in file_system:
                return True
    
    return False


def validate_dependencies(file_system: Dict[str, str]) -> DependencyReport:
    """
    Validate all dependencies across the project.
    
    Checks:
    1. All imports can be resolved
    2. package.json has required dependencies
    3. shadcn components are properly configured
    """
    report = DependencyReport()
    
    # Load package.json
    package_json_content = file_system.get("package.json", "{}")
    try:
        package_data = json.loads(package_json_content)
        dependencies = {
            **package_data.get("dependencies", {}),
            **package_data.get("devDependencies", {})
        }
    except json.JSONDecodeError:
        report.missing_dependencies.append("Invalid package.json format")
        return report
    
    # Check each file's imports
    for file_path, content in file_system.items():
        if not file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
            continue
        
        imports = extract_imports(content)
        
        for imp in imports:
            # Internal import (@/ or ./)
            if imp.startswith('@/') or imp.startswith('.'):
                if not resolve_internal_import(imp, file_path, file_system):
                    report.unresolved_imports.append(f"{file_path}: Cannot resolve '{imp}'")
            
            # External package
            else:
                package_name = imp.split('/')[0]
                # Skip built-in node modules
                if package_name in ['fs', 'path', 'http', 'https', 'crypto', 'util']:
                    continue
                
                if package_name not in dependencies:
                    report.missing_dependencies.append(
                        f"{file_path}: Missing dependency '{package_name}'"
                    )
    
    # Check shadcn configuration
    if 'components.json' not in file_system:
        # Check if shadcn components are being used
        uses_shadcn = any('@/components/ui/' in content for content in file_system.values())
        if uses_shadcn:
            report.shadcn_issues.append("Using shadcn components but components.json is missing")
    
    return report