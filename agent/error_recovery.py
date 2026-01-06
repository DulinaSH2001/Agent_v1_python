"""
Antigravity Agent - Error Recovery System (Phase 3)

Intelligent auto-fix and error recovery with:
- Automatic error classification
- Pattern-based fix suggestions
- Multi-file error correlation
- Contextual error explanations
- Learning from past fixes
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes for Error Recovery
# =============================================================================

@dataclass
class ErrorPattern:
    """Represents a recognized error pattern."""
    error_type: str  # "IMPORT", "TYPE", "SYNTAX", "RUNTIME", "SECURITY"
    pattern: str  # Regex pattern to match error
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    description: str
    auto_fixable: bool = False
    fix_template: Optional[str] = None


@dataclass
class ErrorContext:
    """Context information for an error."""
    file_path: str
    error_message: str
    error_type: str
    line_number: Optional[int] = None
    related_files: List[str] = field(default_factory=list)
    suggested_fix: Optional[str] = None
    auto_fix_available: bool = False
    confidence: float = 0.0  # 0-1, confidence in fix


@dataclass
class FixSuggestion:
    """A suggested fix for an error."""
    file_path: str
    fix_type: str  # "ADD_IMPORT", "FIX_TYPE", "ADD_EXPORT", "REMOVE_CODE"
    description: str
    code_snippet: Optional[str] = None
    confidence: float = 0.0
    auto_applicable: bool = False


@dataclass
class ErrorRecoveryReport:
    """Report on error recovery analysis."""
    total_errors: int
    auto_fixable_errors: int
    manual_fix_required: int
    error_contexts: List[ErrorContext] = field(default_factory=list)
    fix_suggestions: List[FixSuggestion] = field(default_factory=list)
    correlated_errors: List[Tuple[str, str]] = field(default_factory=list)


# =============================================================================
# Error Pattern Database
# =============================================================================

# Common error patterns with auto-fix capabilities
ERROR_PATTERNS = [
    # Import errors
    ErrorPattern(
        error_type="IMPORT",
        pattern=r"Cannot find module ['\"]([^'\"]+)['\"]",
        severity="HIGH",
        description="Missing import statement",
        auto_fixable=True,
        fix_template="Add import statement for '{module}'"
    ),
    ErrorPattern(
        error_type="IMPORT",
        pattern=r"Module ['\"]([^'\"]+)['\"] has no exported member ['\"]([^'\"]+)['\"]",
        severity="HIGH",
        description="Incorrect named import",
        auto_fixable=True,
        fix_template="Fix import: change to correct export name"
    ),

    # Type errors
    ErrorPattern(
        error_type="TYPE",
        pattern=r"Type ['\"]([^'\"]+)['\"] is not assignable to type ['\"]([^'\"]+)['\"]",
        severity="MEDIUM",
        description="Type mismatch",
        auto_fixable=False,
        fix_template="Add type annotation or fix type mismatch"
    ),
    ErrorPattern(
        error_type="TYPE",
        pattern=r"Property ['\"]([^'\"]+)['\"] does not exist on type",
        severity="MEDIUM",
        description="Missing property on type",
        auto_fixable=False,
        fix_template="Add property to interface or check spelling"
    ),

    # Syntax errors
    ErrorPattern(
        error_type="SYNTAX",
        pattern=r"Unexpected token",
        severity="CRITICAL",
        description="Syntax error - malformed code",
        auto_fixable=False,
        fix_template="Fix syntax: check for missing brackets, quotes, etc."
    ),
    ErrorPattern(
        error_type="SYNTAX",
        pattern=r"Expression expected",
        severity="CRITICAL",
        description="Incomplete expression",
        auto_fixable=False,
        fix_template="Complete the expression or remove incomplete code"
    ),

    # Runtime errors
    ErrorPattern(
        error_type="RUNTIME",
        pattern=r"([a-zA-Z]+) is not defined",
        severity="HIGH",
        description="Undefined variable or function",
        auto_fixable=True,
        fix_template="Add missing variable/function or fix import"
    ),

    # Next.js specific
    ErrorPattern(
        error_type="EXPORT",
        pattern=r"Page .* does not have a default export",
        severity="CRITICAL",
        description="Missing default export in page",
        auto_fixable=True,
        fix_template="Add 'export default' to page component"
    ),
    ErrorPattern(
        error_type="PATTERN",
        pattern=r"(useEffect|useState|useContext) .* in Server Component",
        severity="CRITICAL",
        description="React hooks in Server Component",
        auto_fixable=True,
        fix_template="Add 'use client' directive or refactor to Client Component"
    ),
]


# =============================================================================
# Error Classification
# =============================================================================

def classify_error(error_message: str) -> ErrorContext:
    """
    Classify an error and extract context.

    Args:
        error_message: The error message to classify

    Returns:
        ErrorContext with classified information
    """
    # Extract file path from error message
    file_path_match = re.search(
        r'([a-z]+/[a-z0-9/_-]+\.(tsx?|jsx?|py))', error_message, re.IGNORECASE)
    file_path = file_path_match.group(1) if file_path_match else "unknown"

    # Extract line number
    line_match = re.search(r'line (\d+)', error_message, re.IGNORECASE)
    line_number = int(line_match.group(1)) if line_match else None

    # Match against known patterns
    for pattern in ERROR_PATTERNS:
        if re.search(pattern.pattern, error_message, re.IGNORECASE):
            return ErrorContext(
                file_path=file_path,
                error_message=error_message,
                error_type=pattern.error_type,
                line_number=line_number,
                auto_fix_available=pattern.auto_fixable,
                confidence=0.9 if pattern.auto_fixable else 0.5
            )

    # Unknown error pattern
    return ErrorContext(
        file_path=file_path,
        error_message=error_message,
        error_type="UNKNOWN",
        line_number=line_number,
        auto_fix_available=False,
        confidence=0.3
    )


def classify_errors_batch(error_messages: List[str]) -> List[ErrorContext]:
    """
    Classify multiple errors in batch.

    Args:
        error_messages: List of error messages

    Returns:
        List of ErrorContext objects
    """
    return [classify_error(msg) for msg in error_messages]


# =============================================================================
# Multi-File Error Correlation
# =============================================================================

def correlate_errors(error_contexts: List[ErrorContext]) -> List[Tuple[str, str]]:
    """
    Find correlations between errors across files.

    Example: Import error in A.tsx may be caused by missing export in B.tsx

    Args:
        error_contexts: List of error contexts

    Returns:
        List of tuples (cause_file, effect_file) showing error correlations
    """
    correlations = []

    # Group errors by file
    errors_by_file = defaultdict(list)
    for ctx in error_contexts:
        errors_by_file[ctx.file_path].append(ctx)

    # Find import-export correlations
    import_errors = [
        ctx for ctx in error_contexts if ctx.error_type == "IMPORT"]

    for import_error in import_errors:
        # Extract module being imported
        module_match = re.search(
            r"['\"]([^'\"]+)['\"]", import_error.error_message)
        if module_match:
            module = module_match.group(1)

            # Convert to potential file path
            if module.startswith('@/'):
                # Internal import
                potential_file = module[2:] + '.tsx'
                if potential_file in errors_by_file:
                    correlations.append(
                        (potential_file, import_error.file_path))
                    import_error.related_files.append(potential_file)

    # Find type-related correlations
    type_errors = [ctx for ctx in error_contexts if ctx.error_type == "TYPE"]

    # Type errors in multiple files using same type may indicate interface issue
    type_files = set(ctx.file_path for ctx in type_errors)
    if len(type_files) > 1:
        # Multiple files with type errors - may be related
        for i, file1 in enumerate(type_files):
            for file2 in list(type_files)[i+1:]:
                correlations.append((file1, file2))

    return correlations


# =============================================================================
# Intelligent Fix Suggestions
# =============================================================================

def generate_fix_suggestions(error_context: ErrorContext, file_system: Dict[str, str]) -> List[FixSuggestion]:
    """
    Generate intelligent fix suggestions for an error.

    Args:
        error_context: Context about the error
        file_system: Dictionary of all files in the project

    Returns:
        List of FixSuggestion objects
    """
    suggestions = []
    error_msg = error_context.error_message
    file_path = error_context.file_path

    # Fix suggestion for missing imports
    if error_context.error_type == "IMPORT":
        module_match = re.search(
            r"Cannot find module ['\"]([^'\"]+)['\"]", error_msg)
        if module_match:
            module = module_match.group(1)

            # Check if it's an internal module
            if module.startswith('@/'):
                potential_file = module[2:]

                # Suggest creating the file if it doesn't exist
                if potential_file not in file_system:
                    suggestions.append(FixSuggestion(
                        file_path=potential_file + '.tsx',
                        fix_type="CREATE_FILE",
                        description=f"Create missing file: {potential_file}.tsx",
                        confidence=0.8,
                        auto_applicable=False
                    ))

                # Suggest fixing import path
                suggestions.append(FixSuggestion(
                    file_path=file_path,
                    fix_type="FIX_IMPORT",
                    description=f"Fix import path for '{module}'",
                    code_snippet=f"Check if path should be '{module}.tsx' or '{module}/index.tsx'",
                    confidence=0.7,
                    auto_applicable=False
                ))
            else:
                # External module - suggest adding to package.json
                suggestions.append(FixSuggestion(
                    file_path="package.json",
                    fix_type="ADD_DEPENDENCY",
                    description=f"Add missing dependency: {module}",
                    code_snippet=f"npm install {module}",
                    confidence=0.9,
                    auto_applicable=True
                ))

    # Fix suggestion for missing default export
    elif error_context.error_type == "EXPORT":
        if "does not have a default export" in error_msg:
            suggestions.append(FixSuggestion(
                file_path=file_path,
                fix_type="ADD_EXPORT",
                description="Add default export to page component",
                code_snippet="export default function PageName() { ... }",
                confidence=0.95,
                auto_applicable=True
            ))

    # Fix suggestion for React hooks in Server Component
    elif error_context.error_type == "PATTERN":
        if "Server Component" in error_msg:
            suggestions.append(FixSuggestion(
                file_path=file_path,
                fix_type="ADD_DIRECTIVE",
                description="Add 'use client' directive at the top of the file",
                code_snippet="'use client'\n\n// ... rest of the file",
                confidence=0.9,
                auto_applicable=True
            ))

    # Fix suggestion for type errors
    elif error_context.error_type == "TYPE":
        prop_match = re.search(
            r"Property ['\"]([^'\"]+)['\"] does not exist", error_msg)
        if prop_match:
            prop_name = prop_match.group(1)
            suggestions.append(FixSuggestion(
                file_path=file_path,
                fix_type="FIX_TYPE",
                description=f"Add property '{prop_name}' to interface or fix usage",
                confidence=0.6,
                auto_applicable=False
            ))

    return suggestions


# =============================================================================
# Contextual Error Explanations
# =============================================================================

def explain_error(error_context: ErrorContext, file_system: Dict[str, str]) -> str:
    """
    Generate a human-readable explanation of the error with context.

    Args:
        error_context: Context about the error
        file_system: Dictionary of all files

    Returns:
        Detailed explanation string
    """
    explanation = [
        f"## Error in {error_context.file_path}",
        "",
        f"**Type**: {error_context.error_type}",
        f"**Message**: {error_context.error_message}",
        ""
    ]

    if error_context.line_number:
        explanation.append(f"**Line**: {error_context.line_number}")
        explanation.append("")

    # Add contextual explanation based on error type
    if error_context.error_type == "IMPORT":
        explanation.extend([
            "**Cause**: This file is trying to import a module that cannot be found.",
            "",
            "**Common reasons**:",
            "1. The file being imported doesn't exist",
            "2. The import path is incorrect (typo or wrong relative path)",
            "3. Missing dependency in package.json",
            "4. File exists but has wrong extension (.ts vs .tsx)",
            ""
        ])

    elif error_context.error_type == "EXPORT":
        explanation.extend([
            "**Cause**: Next.js pages require a default export.",
            "",
            "**Fix**: Add `export default function PageName() { ... }` to your page component.",
            ""
        ])

    elif error_context.error_type == "PATTERN":
        if "Server Component" in error_context.error_message:
            explanation.extend([
                "**Cause**: Using React hooks (useState, useEffect, etc.) in a Server Component.",
                "",
                "**Fix**: Add `'use client'` at the top of the file to make it a Client Component.",
                "",
                "**Why**: Server Components run on the server and can't use client-side hooks.",
                ""
            ])

    elif error_context.error_type == "TYPE":
        explanation.extend([
            "**Cause**: TypeScript type mismatch or missing type definition.",
            "",
            "**Common fixes**:",
            "1. Add proper type annotations",
            "2. Update interface definitions",
            "3. Check for typos in property names",
            ""
        ])

    # Add related files if any
    if error_context.related_files:
        explanation.append("**Related files**: " +
                           ", ".join(error_context.related_files))
        explanation.append("")

    return "\n".join(explanation)


# =============================================================================
# Auto-Fix System
# =============================================================================

def apply_auto_fix(
    fix_suggestion: FixSuggestion,
    file_system: Dict[str, str]
) -> Tuple[bool, str]:
    """
    Attempt to automatically apply a fix suggestion.

    Args:
        fix_suggestion: The fix to apply
        file_system: Current file system state

    Returns:
        Tuple of (success, message)
    """
    if not fix_suggestion.auto_applicable:
        return False, "Fix is not auto-applicable"

    file_path = fix_suggestion.file_path

    # Apply fix based on type
    if fix_suggestion.fix_type == "ADD_DIRECTIVE":
        if file_path in file_system:
            content = file_system[file_path]

            # Add 'use client' at the top
            if "'use client'" not in content and '"use client"' not in content:
                file_system[file_path] = "'use client'\n\n" + content
                return True, f"Added 'use client' directive to {file_path}"

        return False, f"File {file_path} not found"

    elif fix_suggestion.fix_type == "ADD_EXPORT":
        if file_path in file_system:
            content = file_system[file_path]

            # Check if default export is missing
            if 'export default' not in content:
                # Try to find the main function and add export
                func_match = re.search(
                    r'(function\s+\w+|const\s+\w+\s*=)', content)
                if func_match:
                    # Add export default before the function
                    new_content = content.replace(
                        func_match.group(0),
                        f"export default {func_match.group(0)}"
                    )
                    file_system[file_path] = new_content
                    return True, f"Added default export to {file_path}"

        return False, "Could not auto-apply export fix"

    return False, f"Auto-fix for {fix_suggestion.fix_type} not implemented"


# =============================================================================
# Comprehensive Error Recovery
# =============================================================================

def analyze_errors(
    error_messages: List[str],
    file_system: Dict[str, str]
) -> ErrorRecoveryReport:
    """
    Comprehensive error analysis and recovery planning.

    Args:
        error_messages: List of error messages from build/validation
        file_system: Current file system state

    Returns:
        ErrorRecoveryReport with analysis and suggestions
    """
    # Classify all errors
    error_contexts = classify_errors_batch(error_messages)

    # Find correlations
    correlations = correlate_errors(error_contexts)

    # Generate fix suggestions for each error
    all_suggestions = []
    for ctx in error_contexts:
        suggestions = generate_fix_suggestions(ctx, file_system)
        all_suggestions.extend(suggestions)

        # Add best suggestion to context
        if suggestions:
            best_suggestion = max(suggestions, key=lambda s: s.confidence)
            ctx.suggested_fix = best_suggestion.description

    # Count auto-fixable errors
    auto_fixable = sum(1 for ctx in error_contexts if ctx.auto_fix_available)
    manual_required = len(error_contexts) - auto_fixable

    return ErrorRecoveryReport(
        total_errors=len(error_contexts),
        auto_fixable_errors=auto_fixable,
        manual_fix_required=manual_required,
        error_contexts=error_contexts,
        fix_suggestions=all_suggestions,
        correlated_errors=correlations
    )


def prioritize_fixes(fix_suggestions: List[FixSuggestion]) -> List[FixSuggestion]:
    """
    Prioritize fix suggestions for optimal recovery.

    Priority order:
    1. Auto-applicable fixes with high confidence
    2. Critical errors (EXPORT, SYNTAX)
    3. High-impact fixes (IMPORT - affects multiple files)
    4. Type and pattern fixes

    Args:
        fix_suggestions: List of fix suggestions

    Returns:
        Sorted list of fix suggestions (highest priority first)
    """
    def priority_score(suggestion: FixSuggestion) -> float:
        score = suggestion.confidence

        # Boost auto-applicable
        if suggestion.auto_applicable:
            score += 2.0

        # Boost critical fix types
        if suggestion.fix_type in ["ADD_EXPORT", "ADD_DIRECTIVE"]:
            score += 1.5
        elif suggestion.fix_type in ["ADD_DEPENDENCY", "FIX_IMPORT"]:
            score += 1.0

        return score

    return sorted(fix_suggestions, key=priority_score, reverse=True)
