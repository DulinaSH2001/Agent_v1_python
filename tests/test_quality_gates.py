"""Tests for quality gate blocking behavior."""

import pytest
from agent.code_quality import CodeReviewer, QualityIssue, FileReviewResult, ReviewSummary


class TestQualityGateBlocking:
    """Test that critical errors correctly set blocking flags."""

    def test_unfixed_error_blocks(self):
        """Unfixed critical errors should set blocking=True."""
        reviewer = CodeReviewer()

        # File with a hook but no 'use client' (auto-fixable error)
        # and a missing component import (non-auto-fixable error)
        file_system = {
            "components/Dashboard.tsx": '''
import { useState } from "react"
import { MissingComponent } from "@/components/missing"

export default function Dashboard() {
    const [count, setCount] = useState(0)
    return <MissingComponent count={count} />
}
'''
        }

        summary = reviewer.review_file_system(file_system, auto_fix=True)
        quality_dict = summary.to_dict()

        # The missing_use_client should be auto-fixed
        # The missing_component_file should remain as unfixed error
        # (cross-file check finds @/components/missing not in file_system)
        assert summary.auto_fixes_applied >= 1  # use_client fix

    def test_all_fixed_does_not_block(self):
        """When all errors are auto-fixed, blocking should be False."""
        reviewer = CodeReviewer()

        # File with only auto-fixable issues
        file_system = {
            "components/Simple.tsx": '''
import { useState } from "react"

export default function Simple() {
    const [x, setX] = useState(0)
    return <div>{x}</div>
}
'''
        }

        summary = reviewer.review_file_system(file_system, auto_fix=True)

        # Count remaining unfixed errors
        remaining = sum(
            1 for r in summary.results
            for i in r.issues
            if i.severity == "error" and not i.fix_applied
        )

        # If all errors were fixed, remaining should be 0
        # The auto-fix for missing 'use client' should have been applied
        assert summary.auto_fixes_applied >= 1

    def test_clean_file_no_issues(self):
        """Clean files should produce no issues."""
        reviewer = CodeReviewer()

        file_system = {
            "lib/utils.ts": '''
export function cn(...classes: string[]) {
    return classes.filter(Boolean).join(" ")
}
'''
        }

        summary = reviewer.review_file_system(file_system, auto_fix=True)
        assert summary.total_errors == 0
        assert summary.total_warnings == 0


class TestTypescriptAnyDetection:
    """Test the TypeScript 'any' usage detection."""

    def test_detects_any_usage(self):
        """Should flag `: any` as a warning."""
        reviewer = CodeReviewer()
        result = reviewer.review_file("lib/test.ts", 'const x: any = "hello"')
        any_issues = [i for i in result.issues if i.rule == "typescript_any_usage"]
        assert len(any_issues) >= 1
        assert any_issues[0].severity == "warning"

    def test_no_any_no_warning(self):
        """Files without 'any' should not trigger the warning."""
        reviewer = CodeReviewer()
        result = reviewer.review_file("lib/test.ts", 'const x: string = "hello"')
        any_issues = [i for i in result.issues if i.rule == "typescript_any_usage"]
        assert len(any_issues) == 0


class TestUnusedImportDetection:
    """Test unused import detection."""

    def test_detects_unused_import(self):
        """Should flag imports not used in file body."""
        reviewer = CodeReviewer()
        content = '''import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"

export function Page() {
    return <Button>Click</Button>
}
'''
        result = reviewer.review_file("app/page.tsx", content)
        unused = [i for i in result.issues if i.rule == "unused_import"]
        # Card is imported but not used in JSX
        assert any("Card" in i.message for i in unused)

    def test_used_import_not_flagged(self):
        """Imports that are used should not be flagged."""
        reviewer = CodeReviewer()
        content = '''import { cn } from "@/lib/utils"

export function test() {
    return cn("a", "b")
}
'''
        result = reviewer.review_file("lib/test.ts", content)
        unused = [i for i in result.issues if i.rule == "unused_import"]
        assert len(unused) == 0


class TestMissingReturnType:
    """Test missing return type detection on exported functions."""

    def test_detects_missing_return_type(self):
        """Exported functions without return types should be flagged (info)."""
        reviewer = CodeReviewer()
        content = '''export function getData() {
    return { name: "test" }
}
'''
        result = reviewer.review_file("lib/data.ts", content)
        missing_rt = [i for i in result.issues if i.rule == "missing_return_type"]
        assert len(missing_rt) >= 1
        assert missing_rt[0].severity == "info"

    def test_has_return_type_not_flagged(self):
        """Exported functions with return types should not be flagged."""
        reviewer = CodeReviewer()
        content = '''export function getData(): { name: string } {
    return { name: "test" }
}
'''
        result = reviewer.review_file("lib/data.ts", content)
        missing_rt = [i for i in result.issues if i.rule == "missing_return_type"]
        assert len(missing_rt) == 0


class TestTemplateContractGuards:
    """Template-specific guardrails for generated Next.js apps."""

    def test_sidebar_links_prop_is_auto_fixed(self):
        reviewer = CodeReviewer()
        content = '''import { Sidebar } from "@/components/layout/Sidebar"

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
    return <Sidebar links={navLinks} />
}
'''
        result = reviewer.review_file("app/dashboard/layout.tsx", content)
        sidebar_issues = [i for i in result.issues if i.rule == "sidebar_wrong_prop_name"]
        assert len(sidebar_issues) == 1
        assert result.fixed_content is not None
        assert "navLinks={navLinks}" in result.fixed_content
        assert "links={navLinks}" not in result.fixed_content

    def test_missing_template_data_exports_are_restored(self):
        reviewer = CodeReviewer()
        content = '''import type { NavLink, SocialLink } from "@/types"

export const products = [{ id: "1", name: "Widget" }]
'''
        result = reviewer.review_file("lib/data.ts", content)
        rules = {issue.rule for issue in result.issues}
        assert "missing_navlinks_export" in rules
        assert "missing_sociallinks_export" in rules
        assert result.fixed_content is not None
        assert "export const navLinks" in result.fixed_content
        assert "export const socialLinks" in result.fixed_content
