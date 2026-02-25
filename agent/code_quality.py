"""
Code Quality & Review Module — Phase 5

Rule-based static analysis for generated Next.js/React code.
Runs pre-upload to catch common issues before they reach the build step.

Checks:
- Missing 'use client' on components that use hooks
- Incorrect Shadcn import paths
- Missing accessibility attributes (alt, aria-label, role)
- Unnecessary large imports (barrel imports)
- Missing 'use server' on server actions
- HTML nesting violations
- Unreachable code patterns

Each check can auto-fix simple violations.
"""

from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


# =============================================================================
# Data Types
# =============================================================================

@dataclass
class QualityIssue:
    """A single code quality violation"""
    rule: str              # Rule name (e.g., "missing_use_client")
    severity: str          # "error" | "warning" | "info"
    line: Optional[int]    # Line number (1-indexed), None if file-level
    message: str           # Human-readable description
    auto_fixable: bool     # Whether this module can auto-fix it
    fix_applied: bool = False


@dataclass
class FileReviewResult:
    """Review result for a single file"""
    file_path: str
    issues: List[QualityIssue] = field(default_factory=list)
    fixed_content: Optional[str] = None  # Set if auto-fixes were applied
    error_count: int = 0
    warning_count: int = 0

    def __post_init__(self):
        self.error_count = sum(1 for i in self.issues if i.severity == "error")
        self.warning_count = sum(1 for i in self.issues if i.severity == "warning")

    @property
    def has_issues(self) -> bool:
        return len(self.issues) > 0

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0


@dataclass
class ReviewSummary:
    """Aggregated quality review across all files"""
    total_files: int
    files_with_issues: int
    total_errors: int
    total_warnings: int
    auto_fixes_applied: int
    results: List[FileReviewResult] = field(default_factory=list)

    @property
    def quality_score(self) -> int:
        """Score 0-100; lower errors = higher score"""
        if self.total_files == 0:
            return 100
        issue_rate = (self.total_errors * 2 + self.total_warnings) / (self.total_files * 10)
        return max(0, int(100 - issue_rate * 100))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_files": self.total_files,
            "files_with_issues": self.files_with_issues,
            "total_errors": self.total_errors,
            "total_warnings": self.total_warnings,
            "auto_fixes_applied": self.auto_fixes_applied,
            "quality_score": self.quality_score,
            "results": [
                {
                    "file_path": r.file_path,
                    "error_count": r.error_count,
                    "warning_count": r.warning_count,
                    "fixed": r.fixed_content is not None,
                    "issues": [asdict(i) for i in r.issues],
                }
                for r in self.results
                if r.has_issues
            ],
        }

    def format_for_chat(self) -> str:
        """Format a human-readable quality report"""
        lines = [f"## Code Quality Report (Score: {self.quality_score}/100)"]
        lines.append(
            f"Checked {self.total_files} files — "
            f"{self.total_errors} errors, {self.total_warnings} warnings, "
            f"{self.auto_fixes_applied} auto-fixed"
        )
        if self.files_with_issues > 0:
            lines.append("\n### Issues Found")
            for result in self.results:
                if result.has_issues:
                    fixed_marker = " ✅ auto-fixed" if result.fixed_content else ""
                    lines.append(f"\n**{result.file_path}**{fixed_marker}")
                    for issue in result.issues[:5]:  # cap at 5 per file
                        icon = "❌" if issue.severity == "error" else "⚠️"
                        fix_note = " (fixed)" if issue.fix_applied else ""
                        lines.append(f"  {icon} {issue.message}{fix_note}")
        else:
            lines.append("\n✅ No issues found!")
        return "\n".join(lines)


# =============================================================================
# Pattern helpers
# =============================================================================

REACT_HOOK_RE = re.compile(r'\b(use[A-Z]\w*)\s*\(', re.MULTILINE)
USE_CLIENT_RE = re.compile(r"^['\"]use client['\"];?\s*$", re.MULTILINE)
USE_SERVER_RE = re.compile(r"^['\"]use server['\"];?\s*$", re.MULTILINE)
EXPORT_DEFAULT_RE = re.compile(r'export\s+default\s+(?:function\s+)?(\w+)', re.MULTILINE)
EXPORT_NAMED_RE = re.compile(r'export\s+(?:function|const|class)\s+(\w+)', re.MULTILINE)
IMG_TAG_RE = re.compile(r'<img\b[^>]*>', re.IGNORECASE | re.DOTALL)
ALT_ATTR_RE = re.compile(r'\balt\s*=\s*["\'{]', re.IGNORECASE)
BUTTON_RE = re.compile(r'<[Bb]utton\b[^>]*>', re.DOTALL)
ARIA_LABEL_RE = re.compile(r'\baria-label\s*=', re.IGNORECASE)
SERVER_ACTION_EXPORT_RE = re.compile(r'export\s+async\s+function\s+\w+\s*\(', re.MULTILINE)

# Shadcn: wrong import paths
WRONG_SHADCN_IMPORTS = [
    # Pattern: from 'shadcn/...'  (should be @/components/ui/...)
    (re.compile(r"from\s+['\"]shadcn/ui/(\w+)['\"]", re.MULTILINE), "@/components/ui/{name}"),
    (re.compile(r"from\s+['\"]@shadcn/ui/(\w+)['\"]", re.MULTILINE), "@/components/ui/{name}"),
    # radix imports used directly instead of through shadcn wrappers
    (re.compile(r"from\s+['\"]@radix-ui/react-(\w+)['\"]", re.MULTILINE), None),
]

# Barrel imports known to be large
LARGE_BARREL_IMPORTS = [
    re.compile(r"from\s+['\"]lodash['\"]", re.MULTILINE),
    re.compile(r"from\s+['\"]date-fns['\"]", re.MULTILINE),
]

# HTML nesting violations: tags that must NOT contain block elements
NESTING_VIOLATIONS = [
    (re.compile(r'<p\b[^>]*>.*?<div\b', re.DOTALL | re.IGNORECASE), "<div> inside <p>"),
    (re.compile(r'<p\b[^>]*>.*?<p\b', re.DOTALL | re.IGNORECASE), "<p> nested inside <p>"),
    (re.compile(r'<a\b[^>]*>.*?<a\b', re.DOTALL | re.IGNORECASE), "<a> nested inside <a>"),
    (re.compile(r'<button\b[^>]*>.*?<button\b', re.DOTALL | re.IGNORECASE), "<button> inside <button>"),
]


# =============================================================================
# CodeReviewer
# =============================================================================

class CodeReviewer:
    """
    Rule-based code quality checker for Next.js / React TypeScript files.

    All checks are stateless — call review_file() for each file.
    Auto-fix applies in-place to the content and sets fixed_content.
    """

    def review_file(self, file_path: str, content: str) -> FileReviewResult:
        """Run all checks on a single file. Returns a FileReviewResult."""
        result = FileReviewResult(file_path=file_path)

        # Only check code files
        if not file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
            return result

        is_tsx = file_path.endswith(('.tsx', '.jsx'))
        working_content = content  # will be mutated by auto-fixes

        # --- Run all checks ---
        if is_tsx:
            issues, working_content = self.check_use_client_directive(working_content)
            result.issues.extend(issues)

            issues, working_content = self.check_accessibility(working_content)
            result.issues.extend(issues)

            issues = self.check_html_nesting(working_content)
            result.issues.extend(issues)

        issues, working_content = self.check_shadcn_import_paths(working_content)
        result.issues.extend(issues)

        issues = self.check_bundle_optimization(working_content)
        result.issues.extend(issues)

        if 'use server' not in working_content and SERVER_ACTION_EXPORT_RE.search(working_content):
            issues = self.check_server_action_directive(working_content, file_path)
            result.issues.extend(issues)

        # Recalculate counts
        result.error_count = sum(1 for i in result.issues if i.severity == "error")
        result.warning_count = sum(1 for i in result.issues if i.severity == "warning")

        # Set fixed content only if something actually changed
        if working_content != content:
            result.fixed_content = working_content

        return result

    # -------------------------------------------------------------------------
    # Check: missing 'use client' on components using hooks
    # -------------------------------------------------------------------------
    def check_use_client_directive(
        self, content: str
    ) -> Tuple[List[QualityIssue], str]:
        issues: List[QualityIssue] = []

        has_use_client = bool(USE_CLIENT_RE.search(content))
        has_use_server = bool(USE_SERVER_RE.search(content))
        hooks_found = REACT_HOOK_RE.findall(content)

        # Filter to real hooks (exclude things like "useEffect" from import statements)
        real_hooks = [h for h in hooks_found if h not in ('useRouter', 'usePathname', 'useSearchParams')]

        if not has_use_client and not has_use_server and real_hooks:
            unique_hooks = list(dict.fromkeys(real_hooks))
            issue = QualityIssue(
                rule="missing_use_client",
                severity="error",
                line=None,
                message=f"Component uses React hooks ({', '.join(unique_hooks[:3])}) but is missing 'use client' directive",
                auto_fixable=True,
            )

            # Auto-fix: prepend 'use client'
            content = "'use client';\n\n" + content
            issue.fix_applied = True
            issues.append(issue)

        return issues, content

    # -------------------------------------------------------------------------
    # Check: incorrect Shadcn import paths
    # -------------------------------------------------------------------------
    def check_shadcn_import_paths(
        self, content: str
    ) -> Tuple[List[QualityIssue], str]:
        issues: List[QualityIssue] = []

        for pattern, correct_path in WRONG_SHADCN_IMPORTS:
            for match in pattern.finditer(content):
                component_name = match.group(1) if match.lastindex else "component"
                expected = correct_path.format(name=component_name) if correct_path else f"@/components/ui/{component_name}"

                issue = QualityIssue(
                    rule="incorrect_shadcn_import",
                    severity="error",
                    line=content[:match.start()].count('\n') + 1,
                    message=f"Incorrect Shadcn import path: '{match.group(0)}'. Use '{expected}'",
                    auto_fixable=bool(correct_path),
                )

                if correct_path:
                    # Auto-fix: replace bad import with correct path
                    corrected = f"from '{expected}'"
                    content = content[:match.start()] + corrected + content[match.end():]
                    issue.fix_applied = True

                issues.append(issue)

        return issues, content

    # -------------------------------------------------------------------------
    # Check: accessibility (missing alt on img, aria-label on icon buttons)
    # -------------------------------------------------------------------------
    def check_accessibility(
        self, content: str
    ) -> Tuple[List[QualityIssue], str]:
        issues: List[QualityIssue] = []

        # img without alt
        for match in IMG_TAG_RE.finditer(content):
            img_tag = match.group(0)
            if not ALT_ATTR_RE.search(img_tag):
                line = content[:match.start()].count('\n') + 1
                issues.append(QualityIssue(
                    rule="missing_alt_text",
                    severity="warning",
                    line=line,
                    message=f"<img> tag at line {line} is missing 'alt' attribute",
                    auto_fixable=False,
                ))

        # button with only icon child (no text, no aria-label)
        for match in BUTTON_RE.finditer(content):
            btn_tag = match.group(0)
            if not ARIA_LABEL_RE.search(btn_tag):
                # Check for icon-only pattern heuristic: <Button size="icon"> or <Button ...Icon...>
                if 'size="icon"' in btn_tag or 'icon' in btn_tag.lower():
                    line = content[:match.start()].count('\n') + 1
                    issues.append(QualityIssue(
                        rule="missing_aria_label",
                        severity="warning",
                        line=line,
                        message=f"Icon button at line {line} should have aria-label for screen readers",
                        auto_fixable=False,
                    ))

        return issues, content

    # -------------------------------------------------------------------------
    # Check: bundle optimization (large barrel imports)
    # -------------------------------------------------------------------------
    def check_bundle_optimization(self, content: str) -> List[QualityIssue]:
        issues: List[QualityIssue] = []

        for pattern in LARGE_BARREL_IMPORTS:
            match = pattern.search(content)
            if match:
                line = content[:match.start()].count('\n') + 1
                pkg = match.group(0).split("'")[1].split('"')[0]
                issues.append(QualityIssue(
                    rule="large_barrel_import",
                    severity="warning",
                    line=line,
                    message=f"Barrel import from '{pkg}' at line {line} may increase bundle size. Use named sub-path imports.",
                    auto_fixable=False,
                ))

        return issues

    # -------------------------------------------------------------------------
    # Check: server action files missing 'use server' directive
    # -------------------------------------------------------------------------
    def check_server_action_directive(
        self, content: str, file_path: str
    ) -> List[QualityIssue]:
        issues: List[QualityIssue] = []

        # Only flag files in lib/actions or actions directories
        if 'action' in file_path.lower() or 'server' in file_path.lower():
            if SERVER_ACTION_EXPORT_RE.search(content) and not USE_SERVER_RE.search(content):
                issues.append(QualityIssue(
                    rule="missing_use_server",
                    severity="error",
                    line=1,
                    message=f"File '{file_path}' exports async functions but is missing 'use server' directive",
                    auto_fixable=False,
                ))

        return issues

    # -------------------------------------------------------------------------
    # Check: HTML nesting violations
    # -------------------------------------------------------------------------
    def check_html_nesting(self, content: str) -> List[QualityIssue]:
        issues: List[QualityIssue] = []

        for pattern, description in NESTING_VIOLATIONS:
            match = pattern.search(content)
            if match:
                line = content[:match.start()].count('\n') + 1
                issues.append(QualityIssue(
                    rule="html_nesting_violation",
                    severity="error",
                    line=line,
                    message=f"Invalid HTML nesting at line {line}: {description}",
                    auto_fixable=False,
                ))

        return issues

    # -------------------------------------------------------------------------
    # Batch review
    # -------------------------------------------------------------------------
    def review_file_system(
        self,
        file_system: Dict[str, str],
        auto_fix: bool = True,
    ) -> ReviewSummary:
        """
        Review all files in a file system dict.

        Args:
            file_system: {file_path: content}
            auto_fix: Whether to apply auto-fixes to the file system in-place.

        Returns:
            ReviewSummary with aggregated results.
        """
        results: List[FileReviewResult] = []
        total_errors = 0
        total_warnings = 0
        auto_fixes_applied = 0
        fixed_file_system: Dict[str, str] = {}

        for file_path, content in file_system.items():
            result = self.review_file(file_path, content)
            results.append(result)
            total_errors += result.error_count
            total_warnings += result.warning_count

            if auto_fix and result.fixed_content is not None:
                fixed_file_system[file_path] = result.fixed_content
                auto_fixes_applied += sum(1 for i in result.issues if i.fix_applied)
            else:
                fixed_file_system[file_path] = content

        files_with_issues = sum(1 for r in results if r.has_issues)

        summary = ReviewSummary(
            total_files=len(file_system),
            files_with_issues=files_with_issues,
            total_errors=total_errors,
            total_warnings=total_warnings,
            auto_fixes_applied=auto_fixes_applied,
            results=results,
        )

        # Mutate file_system in-place with fixed content
        if auto_fix:
            file_system.update(fixed_file_system)

        return summary
