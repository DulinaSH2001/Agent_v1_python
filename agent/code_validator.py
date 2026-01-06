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


@dataclass
class CodeQualityMetrics:
    """Metrics for code quality assessment."""
    file_path: str
    lines_of_code: int
    cyclomatic_complexity: int
    nesting_depth: int
    function_count: int
    avg_function_length: float
    type_coverage: float  # % of typed variables/functions
    comment_ratio: float  # % of lines that are comments
    duplication_score: float  # 0-1, lower is better
    maintainability_index: float  # 0-100, higher is better

    @property
    def quality_score(self) -> float:
        """Calculate overall quality score (0-100)."""
        # Weighted scoring
        scores = [
            min(100, max(0, 100 - self.cyclomatic_complexity * 2)),  # 30%
            min(100, max(0, 100 - self.nesting_depth * 10)),  # 20%
            self.type_coverage * 100,  # 20%
            self.maintainability_index,  # 20%
            min(100, (1 - self.duplication_score) * 100),  # 10%
        ]
        weights = [0.3, 0.2, 0.2, 0.2, 0.1]
        return sum(s * w for s, w in zip(scores, weights))

    @property
    def quality_grade(self) -> str:
        """Get quality grade A-F."""
        score = self.quality_score
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"


@dataclass
class AccessibilityReport:
    """Report on accessibility (WCAG) validation."""
    missing_alt_text: List[str] = field(default_factory=list)
    missing_aria_labels: List[str] = field(default_factory=list)
    improper_heading_hierarchy: List[str] = field(default_factory=list)
    missing_form_labels: List[str] = field(default_factory=list)
    interactive_without_role: List[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return (
            len(self.missing_alt_text) > 0 or
            len(self.missing_aria_labels) > 0 or
            len(self.improper_heading_hierarchy) > 0 or
            len(self.missing_form_labels) > 0 or
            len(self.interactive_without_role) > 0
        )

    @property
    def wcag_compliance_score(self) -> float:
        """Calculate WCAG compliance score (0-100)."""
        total_issues = (
            len(self.missing_alt_text) +
            len(self.missing_aria_labels) +
            len(self.improper_heading_hierarchy) +
            len(self.missing_form_labels) +
            len(self.interactive_without_role)
        )
        # Deduct 5 points per issue, minimum 0
        return max(0, 100 - (total_issues * 5))


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
        http_methods = ['GET', 'POST', 'PUT',
                        'DELETE', 'PATCH', 'HEAD', 'OPTIONS']
        has_method_export = any(
            f'export async function {method}' in code for method in http_methods)

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
    react_hooks = ['useState', 'useEffect',
                   'useReducer', 'useContext', 'useRef']
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
                    report.unresolved_imports.append(
                        f"{file_path}: Cannot resolve '{imp}'")

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
        uses_shadcn = any(
            '@/components/ui/' in content for content in file_system.values())
        if uses_shadcn:
            report.shadcn_issues.append(
                "Using shadcn components but components.json is missing")

    return report


# =============================================================================
# PHASE 2: Advanced Security Validation
# =============================================================================

def validate_security_advanced(code: str, file_path: str) -> List[ValidationIssue]:
    """
    Advanced security validation (Phase 2).

    Additional checks:
    - CSRF protection in forms
    - Authentication bypass patterns
    - Rate limiting on API routes
    - Secure cookie settings
    - CORS misconfiguration
    - File upload validation
    """
    issues = []
    has_use_server = "'use server'" in code or '"use server"' in code
    is_route_handler = '/route.ts' in file_path or '/route.tsx' in file_path

    # Check: Forms without CSRF protection
    if '<form' in code and has_use_server:
        if 'csrf' not in code.lower() and 'token' not in code.lower():
            issues.append(ValidationIssue(
                "WARNING", "SECURITY",
                "Form submission without CSRF token - add CSRF protection for state-changing operations"
            ))

    # Check: API routes without rate limiting
    if is_route_handler:
        http_methods = ['POST', 'PUT', 'DELETE', 'PATCH']
        has_mutating_method = any(
            f'export async function {m}' in code for m in http_methods)

        if has_mutating_method and 'ratelimit' not in code.lower():
            issues.append(ValidationIssue(
                "WARNING", "SECURITY",
                "API route without rate limiting - consider adding rate limiting to prevent abuse"
            ))

    # Check: Authentication bypass patterns
    auth_bypass_patterns = [
        (r'if\s*\(\s*!.*auth.*\)\s*{\s*//\s*return',
         "Commented out auth check"),
        (r'auth\s*=\s*true\s*;?\s*//\s*TODO', "Hardcoded auth bypass"),
        (r'// TEMP.*auth', "Temporary auth bypass"),
    ]

    for pattern, description in auth_bypass_patterns:
        if re.search(pattern, code, re.IGNORECASE):
            issues.append(ValidationIssue(
                "ERROR", "SECURITY",
                f"CRITICAL: {description} detected - remove auth bypass before production"
            ))

    # Check: Insecure cookie settings
    if 'cookie' in code.lower():
        if 'httpOnly' not in code and 'httponly' not in code:
            issues.append(ValidationIssue(
                "WARNING", "SECURITY",
                "Cookie without httpOnly flag - enable httpOnly to prevent XSS attacks"
            ))
        if 'secure' not in code.lower():
            issues.append(ValidationIssue(
                "WARNING", "SECURITY",
                "Cookie without secure flag - enable secure flag for HTTPS-only transmission"
            ))
        if 'sameSite' not in code:
            issues.append(ValidationIssue(
                "WARNING", "SECURITY",
                "Cookie without sameSite attribute - add sameSite to prevent CSRF"
            ))

    # Check: CORS misconfiguration
    if 'cors' in code.lower() or 'Access-Control' in code:
        if "'*'" in code or '"*"' in code:
            issues.append(ValidationIssue(
                "ERROR", "SECURITY",
                "CRITICAL: CORS configured with wildcard (*) - specify exact allowed origins"
            ))

    # Check: File upload without validation
    if 'file' in code.lower() and ('upload' in code.lower() or 'formdata' in code.lower()):
        has_validation = any(keyword in code.lower() for keyword in [
            'mime', 'type', 'extension', 'size', 'maxsize', 'accept'
        ])
        if not has_validation:
            issues.append(ValidationIssue(
                "ERROR", "SECURITY",
                "File upload without validation - validate file type, size, and content"
            ))

    # Check: Regex DoS vulnerability
    dangerous_regex_patterns = [
        r'\(\.\*\)\+',  # (.*)+
        r'\(\.\+\)\+',  # (.+)+
        r'\([^)]*\)\*\+',  # Complex nested quantifiers
    ]

    for pattern in dangerous_regex_patterns:
        if re.search(pattern, code):
            issues.append(ValidationIssue(
                "WARNING", "SECURITY",
                "Potentially vulnerable regex pattern - avoid nested quantifiers (ReDoS risk)"
            ))

    # Check: Password handling
    if 'password' in code.lower():
        if 'bcrypt' not in code and 'argon2' not in code and 'scrypt' not in code:
            issues.append(ValidationIssue(
                "ERROR", "SECURITY",
                "Password handling without proper hashing - use bcrypt, argon2, or scrypt"
            ))
        if 'console.log' in code and 'password' in code.lower():
            issues.append(ValidationIssue(
                "ERROR", "SECURITY",
                "CRITICAL: Password may be logged - remove console.log statements with passwords"
            ))

    return issues


# =============================================================================
# PHASE 2: Code Quality Metrics
# =============================================================================

def calculate_cyclomatic_complexity(code: str) -> int:
    """
    Calculate cyclomatic complexity.

    Counts decision points: if, for, while, case, &&, ||, ?, catch
    Complexity = decision_points + 1
    """
    complexity = 1  # Base complexity

    # Count decision points
    decision_keywords = [
        r'\bif\b', r'\bfor\b', r'\bwhile\b', r'\bcase\b',
        r'\bcatch\b', r'\b\?\s*', r'&&', r'\|\|'
    ]

    for keyword in decision_keywords:
        complexity += len(re.findall(keyword, code))

    return complexity


def calculate_nesting_depth(code: str) -> int:
    """Calculate maximum nesting depth."""
    max_depth = 0
    current_depth = 0

    for char in code:
        if char == '{':
            current_depth += 1
            max_depth = max(max_depth, current_depth)
        elif char == '}':
            current_depth = max(0, current_depth - 1)

    return max_depth


def count_functions(code: str) -> int:
    """Count number of functions in code."""
    # Match function declarations
    patterns = [
        r'\bfunction\s+\w+',  # function name()
        r'\bconst\s+\w+\s*=\s*\([^)]*\)\s*=>',  # const name = () =>
        r'\blet\s+\w+\s*=\s*\([^)]*\)\s*=>',  # let name = () =>
        r'\basync\s+function\s+\w+',  # async function name()
    ]

    count = 0
    for pattern in patterns:
        count += len(re.findall(pattern, code))

    return max(1, count)  # Avoid division by zero


def calculate_type_coverage(code: str) -> float:
    """
    Calculate TypeScript type coverage (0-1).

    Measures: typed parameters, typed variables, typed return types
    """
    if not code.strip():
        return 0.0

    # Count type annotations
    type_annotations = len(re.findall(r':\s*\w+', code))

    # Count variables and parameters
    variables = len(re.findall(r'\b(?:const|let|var)\s+\w+', code))
    parameters = len(re.findall(r'\([^)]*\w+[^)]*\)', code))

    total_typeable = max(1, variables + parameters)

    return min(1.0, type_annotations / total_typeable)


def calculate_comment_ratio(code: str) -> float:
    """Calculate ratio of comment lines to total lines."""
    lines = code.split('\n')
    comment_lines = 0

    in_block_comment = False

    for line in lines:
        stripped = line.strip()

        # Block comments
        if '/*' in stripped:
            in_block_comment = True
        if in_block_comment:
            comment_lines += 1
        if '*/' in stripped:
            in_block_comment = False
            continue

        # Single line comments
        if stripped.startswith('//'):
            comment_lines += 1

    total_lines = max(1, len(lines))
    return comment_lines / total_lines


def calculate_duplication_score(code: str) -> float:
    """
    Estimate code duplication (0-1, lower is better).

    Uses simple line-based similarity detection.
    """
    lines = [l.strip() for l in code.split('\n') if l.strip()
             and not l.strip().startswith('//')]

    if len(lines) < 3:
        return 0.0

    # Count repeated 3-line sequences
    sequences = {}
    for i in range(len(lines) - 2):
        seq = '\n'.join(lines[i:i+3])
        sequences[seq] = sequences.get(seq, 0) + 1

    # Calculate duplication
    duplicated_sequences = sum(1 for count in sequences.values() if count > 1)
    total_sequences = max(1, len(sequences))

    return duplicated_sequences / total_sequences


def calculate_maintainability_index(
    lines_of_code: int,
    cyclomatic_complexity: int,
    halstead_volume: float = None
) -> float:
    """
    Calculate maintainability index (0-100, higher is better).

    Simplified formula without Halstead volume:
    MI = max(0, (171 - 5.2 * ln(LOC) - 0.23 * CC) * 100 / 171)
    """
    import math

    if lines_of_code == 0:
        return 100.0

    loc_log = math.log(max(1, lines_of_code))

    # Simplified MI formula
    mi = 171 - 5.2 * loc_log - 0.23 * cyclomatic_complexity

    # Normalize to 0-100
    normalized = max(0, min(100, (mi * 100) / 171))

    return normalized


def analyze_code_quality(code: str, file_path: str) -> CodeQualityMetrics:
    """
    Comprehensive code quality analysis.

    Returns:
        CodeQualityMetrics with all quality metrics
    """
    lines_of_code = len([l for l in code.split('\n') if l.strip()])
    cyclomatic_complexity = calculate_cyclomatic_complexity(code)
    nesting_depth = calculate_nesting_depth(code)
    function_count = count_functions(code)

    # Calculate average function length
    avg_function_length = lines_of_code / function_count if function_count > 0 else 0

    # Calculate type coverage
    type_coverage = calculate_type_coverage(code)

    # Calculate comment ratio
    comment_ratio = calculate_comment_ratio(code)

    # Calculate duplication
    duplication_score = calculate_duplication_score(code)

    # Calculate maintainability index
    maintainability_index = calculate_maintainability_index(
        lines_of_code, cyclomatic_complexity
    )

    return CodeQualityMetrics(
        file_path=file_path,
        lines_of_code=lines_of_code,
        cyclomatic_complexity=cyclomatic_complexity,
        nesting_depth=nesting_depth,
        function_count=function_count,
        avg_function_length=avg_function_length,
        type_coverage=type_coverage,
        comment_ratio=comment_ratio,
        duplication_score=duplication_score,
        maintainability_index=maintainability_index
    )


# =============================================================================
# PHASE 2: Accessibility Validation (WCAG 2.1 AA)
# =============================================================================

def validate_accessibility(code: str, file_path: str) -> AccessibilityReport:
    """
    Validate accessibility (WCAG 2.1 AA compliance).

    Checks:
    - Images have alt text
    - Interactive elements have ARIA labels
    - Proper heading hierarchy
    - Form labels
    - Interactive elements have roles
    """
    report = AccessibilityReport()

    # Only check component files
    if not file_path.endswith('.tsx'):
        return report

    # Check: Images without alt text
    img_pattern = r'<img[^>]*>'
    for match in re.finditer(img_pattern, code):
        img_tag = match.group(0)
        if 'alt=' not in img_tag:
            line_num = code[:match.start()].count('\n') + 1
            report.missing_alt_text.append(
                f"Line {line_num}: <img> without alt attribute"
            )

    # Check: Buttons/Links without accessible names
    interactive_patterns = [
        (r'<button[^>]*>', 'button'),
        (r'<a[^>]*>', 'link'),
    ]

    for pattern, element_type in interactive_patterns:
        for match in re.finditer(pattern, code):
            tag = match.group(0)
            # Check if has aria-label or text content
            tag_end = code.find('>', match.end())
            if tag_end == -1:
                continue

            closing_tag = code.find(f'</{element_type}>', tag_end)
            if closing_tag == -1:
                closing_tag = code.find(
                    '</button>' if element_type == 'button' else '</a>', tag_end)

            if closing_tag != -1:
                content = code[tag_end+1:closing_tag].strip()
                has_accessible_name = (
                    'aria-label=' in tag or
                    'aria-labelledby=' in tag or
                    len(content) > 0
                )

                if not has_accessible_name:
                    line_num = code[:match.start()].count('\n') + 1
                    report.missing_aria_labels.append(
                        f"Line {line_num}: <{element_type}> without accessible name (text or aria-label)"
                    )

    # Check: Heading hierarchy
    headings = re.findall(r'<h([1-6])', code)
    if headings:
        prev_level = 0
        for i, level_str in enumerate(headings):
            level = int(level_str)
            if prev_level > 0 and level > prev_level + 1:
                report.improper_heading_hierarchy.append(
                    f"Heading level skipped: h{prev_level} to h{level} (should be sequential)"
                )
            prev_level = level

    # Check: Form inputs without labels
    input_pattern = r'<input[^>]*>'
    for match in re.finditer(input_pattern, code):
        input_tag = match.group(0)
        has_label = (
            'aria-label=' in input_tag or
            'aria-labelledby=' in input_tag or
            'id=' in input_tag  # Assuming Label exists (simplified)
        )

        if not has_label and 'type="hidden"' not in input_tag:
            line_num = code[:match.start()].count('\n') + 1
            report.missing_form_labels.append(
                f"Line {line_num}: <input> without label or aria-label"
            )

    # Check: Interactive elements without proper role
    clickable_divs = re.finditer(r'<div[^>]*onClick', code)
    for match in clickable_divs:
        div_tag = match.group(0)
        if 'role=' not in div_tag:
            line_num = code[:match.start()].count('\n') + 1
            report.interactive_without_role.append(
                f"Line {line_num}: Interactive <div> without role (add role='button' or use <button>)"
            )

    return report


# =============================================================================
# PHASE 2: Performance Validation
# =============================================================================

def validate_performance(code: str, file_path: str) -> List[ValidationIssue]:
    """
    Performance validation.

    Checks:
    - Bundle size concerns
    - Missing dynamic imports
    - Large dependencies
    - Inefficient patterns
    """
    issues = []

    # Check: Large library imports that should be dynamic
    heavy_libraries = {
        'chart.js': 'Consider dynamic import for chart.js',
        'moment': 'Use date-fns or dayjs instead of moment (smaller bundle)',
        'lodash': 'Import specific lodash functions, not entire library',
    }

    for lib, suggestion in heavy_libraries.items():
        if f"from '{lib}'" in code or f'from "{lib}"' in code:
            if 'import(' not in code:  # No dynamic import
                issues.append(ValidationIssue(
                    "WARNING", "PERFORMANCE",
                    f"{suggestion}"
                ))

    # Check: Missing Next.js Image optimization
    if '<img' in code and 'next/image' not in code:
        issues.append(ValidationIssue(
            "WARNING", "PERFORMANCE",
            "Use next/image instead of <img> for automatic optimization"
        ))

    # Check: Large inline data
    json_match = re.search(r'\{[^}]{500,}\}', code)  # Objects > 500 chars
    if json_match:
        issues.append(ValidationIssue(
            "WARNING", "PERFORMANCE",
            "Large inline object detected - consider moving to separate file or database"
        ))

    # Check: Inefficient array operations
    if 'forEach' in code and 'return' in code:
        issues.append(ValidationIssue(
            "WARNING", "PERFORMANCE",
            "forEach with return - consider using map/filter/reduce for better performance"
        ))

    return issues


# =============================================================================
# PHASE 2: Formatting Validation
# =============================================================================

def validate_formatting(code: str, file_path: str) -> List[ValidationIssue]:
    """
    Validate code formatting.

    Checks:
    - Consistent indentation
    - Line length
    - Import ordering
    """
    issues = []
    lines = code.split('\n')

    # Check: Line length
    for i, line in enumerate(lines, 1):
        if len(line) > 120:
            issues.append(ValidationIssue(
                "WARNING", "FORMATTING",
                f"Line {i} exceeds 120 characters ({len(line)} chars) - consider breaking into multiple lines"
            ))

    # Check: Inconsistent indentation
    indents = []
    for line in lines:
        if line.strip():
            leading_spaces = len(line) - len(line.lstrip())
            if leading_spaces > 0:
                indents.append(leading_spaces)

    if indents:
        # Check if using consistent 2 or 4 space indentation
        uses_2_spaces = all(indent % 2 == 0 for indent in indents)
        uses_4_spaces = all(indent % 4 == 0 for indent in indents)

        if not uses_2_spaces and not uses_4_spaces:
            issues.append(ValidationIssue(
                "WARNING", "FORMATTING",
                "Inconsistent indentation - use consistent 2 or 4 space indentation"
            ))

    # Check: Import ordering (React/Next imports should be first)
    import_lines = [i for i, line in enumerate(
        lines) if line.strip().startswith('import ')]
    if import_lines:
        react_imports = [
            i for i in import_lines if 'react' in lines[i].lower() or 'next/' in lines[i]]
        other_imports = [i for i in import_lines if i not in react_imports]

        if react_imports and other_imports:
            if react_imports[0] > other_imports[0]:
                issues.append(ValidationIssue(
                    "WARNING", "FORMATTING",
                    "Import ordering - place React/Next.js imports before other imports"
                ))

    return issues
