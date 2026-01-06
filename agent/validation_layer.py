"""
Antigravity Agent - Validation Layer

Pre-build validation to catch errors before upload.

This module provides the validation_node that runs comprehensive checks
on all generated code before it gets uploaded to Azure Blob Storage.
This catches 80%+ of compilation errors locally, providing instant feedback
and reducing the need for costly build-fix cycles.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from agent.code_validator import (
    ValidationResult,
    DependencyReport,
    CodeQualityMetrics,
    AccessibilityReport,
    validate_file,
    validate_typescript_advanced,
    validate_security,
    validate_security_advanced,
    validate_dependencies,
    validate_file_size,
    validate_accessibility,
    validate_performance,
    validate_formatting,
    analyze_code_quality,
)
from agent.state_engine import AgentState
from agent.ably_utils import publish_task_progress

logger = logging.getLogger(__name__)


# =============================================================================
# Validation Configuration
# =============================================================================

class ValidationConfig:
    """Configuration for validation thresholds and limits."""

    # File size limits
    MAX_FILE_SIZE_MB = 5.0  # Per file
    MAX_TOTAL_SIZE_MB = 100.0  # Total project

    # Validation modes
    STRICT_MODE = True  # Fail on warnings in critical files
    SECURITY_STRICT = True  # Always fail on security issues

    # Error thresholds
    MAX_ERRORS_PER_FILE = 5  # Stop validating file after N errors
    MAX_TOTAL_ERRORS = 20  # Stop validation after N total errors

    # PHASE 2: Quality thresholds
    MIN_QUALITY_SCORE = 60.0  # Minimum acceptable quality score (D grade)
    MAX_CYCLOMATIC_COMPLEXITY = 15  # Maximum complexity per file
    MIN_TYPE_COVERAGE = 0.5  # Minimum 50% type coverage
    MIN_ACCESSIBILITY_SCORE = 70.0  # Minimum WCAG compliance score

    # PHASE 2: Enable/disable checks
    ENABLE_QUALITY_SCORING = True
    ENABLE_ACCESSIBILITY_CHECKS = True
    ENABLE_PERFORMANCE_CHECKS = True
    ENABLE_FORMATTING_CHECKS = True


# =============================================================================
# Validation Node
# =============================================================================

async def validation_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    Validation node - validates all generated code before upload.

    This node runs comprehensive validation checks:
    1. Basic syntax validation (brackets, quotes, etc.)
    2. TypeScript/TSX pattern validation
    3. React 19 pattern validation
    4. Next.js 16 pattern validation
    5. Security vulnerability scanning
    6. Dependency resolution checking
    7. File size and complexity limits

    If validation fails, it generates fix tasks and routes back to
    generation_node. If validation passes, execution continues to
    persistence_node.

    Args:
        state: Current agent state with file_system
        config: Runnable configuration with thread_id

    Returns:
        State update with:
        - build_status: "validated" | "validation_failed" | "validation_critical"
        - build_logs: List of validation errors/warnings
        - implementation_plan: Fix tasks if validation failed
        - iteration_count: Incremented
    """
    logger.info("validation_node: Starting comprehensive validation")

    file_system = state.get("file_system", {})
    build_logs = list(state.get("build_logs", []))
    thread_id = config.get("configurable", {}).get("thread_id", "job-unknown")

    # Track validation results
    all_errors: List[str] = []
    all_warnings: List[str] = []
    passed_files: List[str] = []
    failed_files: List[str] = []
    security_issues: List[str] = []

    # PHASE 2: Track quality metrics
    quality_metrics: Dict[str, CodeQualityMetrics] = {}
    accessibility_reports: Dict[str, AccessibilityReport] = {}
    low_quality_files: List[str] = []
    accessibility_issues: List[str] = []

    # Count total files to validate
    code_files = [
        fp for fp in file_system.keys()
        if fp.endswith(('.ts', '.tsx', '.js', '.jsx', '.py', '.json'))
    ]

    total_files = len(code_files)
    logger.info(f"validation_node: Validating {total_files} files")

    # =================================================================
    # Step 1: Validate Each File
    # =================================================================

    for idx, file_path in enumerate(code_files):
        content = file_system[file_path]

        # Publish progress
        await publish_task_progress(
            job_id=thread_id,
            task_index=idx + 1,
            total_tasks=total_files,
            file_path=file_path,
            status="validating"
        )

        logger.info(
            f"validation_node: Validating [{idx+1}/{total_files}] {file_path}")

        # Check file size first
        is_size_valid, size_error = validate_file_size(
            file_path, content, ValidationConfig.MAX_FILE_SIZE_MB
        )
        if not is_size_valid:
            all_errors.append(f"{file_path}: {size_error}")
            failed_files.append(file_path)
            continue

        # Basic syntax validation
        is_valid, error = validate_file(file_path, content)
        if not is_valid:
            all_errors.append(f"{file_path}: {error}")
            failed_files.append(file_path)

            # Stop if too many errors in one file
            if len(all_errors) >= ValidationConfig.MAX_TOTAL_ERRORS:
                logger.warning(
                    f"validation_node: Reached error limit ({ValidationConfig.MAX_TOTAL_ERRORS})")
                break

            continue

        # Advanced TypeScript validation
        if file_path.endswith(('.ts', '.tsx')):
            ts_result: ValidationResult = validate_typescript_advanced(
                content, file_path)

            if ts_result.has_errors:
                for err in ts_result.errors:
                    all_errors.append(f"{file_path}: {err.message}")
                failed_files.append(file_path)

                # Stop if too many errors
                if len(all_errors) >= ValidationConfig.MAX_TOTAL_ERRORS:
                    logger.warning(f"validation_node: Reached error limit")
                    break

                continue

            # Collect warnings
            for warn in ts_result.warnings:
                all_warnings.append(f"{file_path}: {warn.message}")

        # Security validation
        security_issues_for_file = validate_security(content, file_path)
        for issue in security_issues_for_file:
            if issue.severity == "ERROR":
                security_issues.append(f"{file_path}: {issue.message}")
                all_errors.append(f"{file_path}: {issue.message}")
            else:
                all_warnings.append(f"{file_path}: {issue.message}")

        # PHASE 2: Advanced security validation
        advanced_security = validate_security_advanced(content, file_path)
        for issue in advanced_security:
            if issue.severity == "ERROR":
                security_issues.append(f"{file_path}: {issue.message}")
                all_errors.append(f"{file_path}: {issue.message}")
            else:
                all_warnings.append(f"{file_path}: {issue.message}")

        # PHASE 2: Code quality analysis
        if ValidationConfig.ENABLE_QUALITY_SCORING and file_path.endswith(('.ts', '.tsx')):
            metrics = analyze_code_quality(content, file_path)
            quality_metrics[file_path] = metrics

            # Check quality thresholds
            if metrics.quality_score < ValidationConfig.MIN_QUALITY_SCORE:
                low_quality_files.append(file_path)
                all_warnings.append(
                    f"{file_path}: Low quality score {metrics.quality_score:.1f}/100 "
                    f"(Grade: {metrics.quality_grade}) - consider refactoring"
                )

            if metrics.cyclomatic_complexity > ValidationConfig.MAX_CYCLOMATIC_COMPLEXITY:
                all_warnings.append(
                    f"{file_path}: High complexity {metrics.cyclomatic_complexity} "
                    f"(max: {ValidationConfig.MAX_CYCLOMATIC_COMPLEXITY}) - consider breaking down functions"
                )

            if metrics.type_coverage < ValidationConfig.MIN_TYPE_COVERAGE:
                all_warnings.append(
                    f"{file_path}: Low type coverage {metrics.type_coverage*100:.1f}% "
                    f"(min: {ValidationConfig.MIN_TYPE_COVERAGE*100:.0f}%) - add more type annotations"
                )

        # PHASE 2: Accessibility validation
        if ValidationConfig.ENABLE_ACCESSIBILITY_CHECKS and file_path.endswith('.tsx'):
            a11y_report = validate_accessibility(content, file_path)
            if a11y_report.has_issues:
                accessibility_reports[file_path] = a11y_report

                # Add accessibility warnings
                for issue in a11y_report.missing_alt_text:
                    accessibility_issues.append(f"{file_path}: {issue}")
                    all_warnings.append(f"{file_path}: {issue}")

                for issue in a11y_report.missing_aria_labels:
                    accessibility_issues.append(f"{file_path}: {issue}")
                    all_warnings.append(f"{file_path}: {issue}")

                for issue in a11y_report.missing_form_labels:
                    accessibility_issues.append(f"{file_path}: {issue}")
                    all_warnings.append(f"{file_path}: {issue}")

                # Check WCAG compliance score
                if a11y_report.wcag_compliance_score < ValidationConfig.MIN_ACCESSIBILITY_SCORE:
                    all_warnings.append(
                        f"{file_path}: WCAG compliance score {a11y_report.wcag_compliance_score:.1f}/100 "
                        f"(min: {ValidationConfig.MIN_ACCESSIBILITY_SCORE}) - improve accessibility"
                    )

        # PHASE 2: Performance validation
        if ValidationConfig.ENABLE_PERFORMANCE_CHECKS:
            perf_issues = validate_performance(content, file_path)
            for issue in perf_issues:
                all_warnings.append(f"{file_path}: {issue.message}")

        # PHASE 2: Formatting validation
        if ValidationConfig.ENABLE_FORMATTING_CHECKS:
            format_issues = validate_formatting(content, file_path)
            for issue in format_issues:
                all_warnings.append(f"{file_path}: {issue.message}")

        # File passed validation
        passed_files.append(file_path)

    # =================================================================
    # Step 2: Validate Dependencies
    # =================================================================

    logger.info("validation_node: Validating dependencies")
    dep_report: DependencyReport = validate_dependencies(file_system)

    if dep_report.has_issues:
        all_errors.extend(dep_report.missing_dependencies)
        all_errors.extend(dep_report.unresolved_imports)
        all_warnings.extend(dep_report.shadcn_issues)

        if dep_report.circular_imports:
            all_errors.append(
                f"Circular imports detected: {', '.join(dep_report.circular_imports)}"
            )

    # =================================================================
    # Step 3: Analyze Results
    # =================================================================

    has_errors = len(all_errors) > 0
    has_critical_security = any("CRITICAL" in err for err in security_issues)

    logger.info(
        f"validation_node: Results - Passed: {len(passed_files)}, Failed: {len(failed_files)}")
    logger.info(
        f"validation_node: Errors: {len(all_errors)}, Warnings: {len(all_warnings)}")
    logger.info(
        f"validation_node: Security issues: {len(security_issues)} (Critical: {has_critical_security})")

    # PHASE 2: Log quality metrics
    if quality_metrics:
        avg_quality = sum(
            m.quality_score for m in quality_metrics.values()) / len(quality_metrics)
        avg_complexity = sum(
            m.cyclomatic_complexity for m in quality_metrics.values()) / len(quality_metrics)
        avg_type_coverage = sum(
            m.type_coverage for m in quality_metrics.values()) / len(quality_metrics)

        logger.info(
            f"validation_node: Quality - Avg Score: {avg_quality:.1f}, "
            f"Avg Complexity: {avg_complexity:.1f}, "
            f"Avg Type Coverage: {avg_type_coverage*100:.1f}%"
        )
        logger.info(
            f"validation_node: Low quality files: {len(low_quality_files)}"
        )

    if accessibility_reports:
        avg_a11y = sum(r.wcag_compliance_score for r in accessibility_reports.values(
        )) / len(accessibility_reports)
        logger.info(
            f"validation_node: Accessibility - Avg WCAG Score: {avg_a11y:.1f}, "
            f"Files with issues: {len(accessibility_reports)}"
        )

    # =================================================================
    # Step 4: Generate Fix Tasks if Needed
    # =================================================================

    if has_errors or has_critical_security:
        logger.warning(
            "validation_node: Validation FAILED - generating fix tasks")

        fix_tasks = []

        # Group errors by file
        errors_by_file: Dict[str, List[str]] = {}
        for error in all_errors:
            if ':' in error:
                file_path = error.split(':')[0].strip()
                error_msg = ':'.join(error.split(':')[1:]).strip()

                if file_path not in errors_by_file:
                    errors_by_file[file_path] = []
                errors_by_file[file_path].append(error_msg)

        # Create fix tasks for each file with errors
        for file_path, errors in errors_by_file.items():
            # Combine errors for this file
            error_summary = '\n- '.join(errors[:5])  # First 5 errors
            if len(errors) > 5:
                error_summary += f"\n- ... and {len(errors) - 5} more errors"

            fix_tasks.append({
                "id": f"fix-validation-{len(fix_tasks)}",
                "type": "modify",
                "file_path": file_path,
                "description": f"Fix validation errors:\n- {error_summary}",
                "dependencies": [],
                "priority": 0 if any("CRITICAL" in e for e in errors) else 1,
                "category": "fix",
                "validation_errors": errors,
            })

        # Update build logs
        build_logs.append(
            f"❌ Validation failed: {len(failed_files)} files with errors")
        build_logs.append(f"Total errors: {len(all_errors)}")
        build_logs.append(f"Security issues: {len(security_issues)}")
        build_logs.append("")
        build_logs.append("First 10 errors:")
        build_logs.extend(all_errors[:10])

        if all_warnings:
            build_logs.append("")
            build_logs.append(f"Warnings ({len(all_warnings)}):")
            build_logs.extend(all_warnings[:5])

        # Determine severity
        if has_critical_security:
            build_status = "validation_critical"
            logger.error("validation_node: CRITICAL security issues detected")
        else:
            build_status = "validation_failed"

        return {
            "implementation_plan": fix_tasks,
            "build_logs": build_logs,
            "build_status": build_status,
            "iteration_count": state.get("iteration_count", 0) + 1,
        }

    # =================================================================
    # Step 5: Validation Passed
    # =================================================================

    else:
        logger.info("validation_node: Validation PASSED ✅")

        # Log success
        build_logs.append(
            f"✅ Validation passed: {len(passed_files)} files validated successfully")

        if all_warnings:
            build_logs.append(f"⚠️  Warnings: {len(all_warnings)}")
            build_logs.extend(all_warnings[:5])

        # Calculate metrics
        total_size = sum(len(content) for content in file_system.values())
        size_mb = total_size / (1024 * 1024)

        build_logs.append("")
        build_logs.append(
            f"📊 Project size: {size_mb:.2f}MB ({len(file_system)} files)")
        build_logs.append(f"📄 Code files: {len(code_files)}")
        build_logs.append(f"✅ Passed: {len(passed_files)}")
        build_logs.append(f"⚠️  Warnings: {len(all_warnings)}")

        # PHASE 2: Add quality metrics summary
        if quality_metrics:
            avg_quality = sum(
                m.quality_score for m in quality_metrics.values()) / len(quality_metrics)
            build_logs.append(f"📈 Avg Quality Score: {avg_quality:.1f}/100")

            grade_counts = {}
            for m in quality_metrics.values():
                grade_counts[m.quality_grade] = grade_counts.get(
                    m.quality_grade, 0) + 1

            grades_str = ', '.join(
                f"{grade}: {count}" for grade, count in sorted(grade_counts.items()))
            build_logs.append(f"🎯 Quality Grades: {grades_str}")

        if accessibility_reports:
            avg_a11y = sum(r.wcag_compliance_score for r in accessibility_reports.values(
            )) / len(accessibility_reports)
            build_logs.append(f"♿ Avg WCAG Score: {avg_a11y:.1f}/100")

        return {
            "build_logs": build_logs,
            "build_status": "validated",
        }


# =============================================================================
# Helper Functions
# =============================================================================

def get_validation_summary(state: AgentState) -> Dict[str, Any]:
    """
    Get a summary of validation results from state.

    Args:
        state: Agent state with build_logs and build_status

    Returns:
        Dictionary with validation summary
    """
    build_status = state.get("build_status", "")
    build_logs = state.get("build_logs", [])

    summary = {
        "status": build_status,
        "passed": build_status == "validated",
        "critical": build_status == "validation_critical",
        "errors": [],
        "warnings": [],
    }

    # Extract errors and warnings from logs
    for log in build_logs:
        if "ERROR" in log or "❌" in log:
            summary["errors"].append(log)
        elif "WARNING" in log or "⚠️" in log:
            summary["warnings"].append(log)

    return summary
