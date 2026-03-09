"""
Tests for the new plan validator rules (7, 8, 9).
"""

from agent.plan_validator import validate_plan


class TestRule7ProjectTypePages:
    """Rule 7: Warn if required pages are missing for detected project type."""

    def test_portfolio_missing_contact_warns(self):
        plan = [
            {"id": "1", "type": "modify", "file_path": "app/page.tsx",
             "description": "Hero landing for portfolio"},
            {"id": "2", "type": "create", "file_path": "app/about/page.tsx",
             "description": "About page"},
            {"id": "3", "type": "create", "file_path": "app/projects/page.tsx",
             "description": "Projects showcase"},
        ]
        _, warnings = validate_plan(plan, {}, {})
        contact_warnings = [w for w in warnings if "contact" in w.lower()]
        assert len(contact_warnings) > 0, "Should warn about missing contact page"

    def test_complete_plan_no_warnings(self):
        plan = [
            {"id": "1", "type": "modify", "file_path": "app/page.tsx",
             "description": "Hero landing portfolio page"},
            {"id": "2", "type": "create", "file_path": "app/about/page.tsx",
             "description": "About page"},
            {"id": "3", "type": "create", "file_path": "app/projects/page.tsx",
             "description": "Projects work showcase"},
            {"id": "4", "type": "create", "file_path": "app/contact/page.tsx",
             "description": "Contact form page"},
        ]
        _, warnings = validate_plan(plan, {}, {})
        type_warnings = [w for w in warnings if "typically includes" in w]
        assert len(type_warnings) == 0, \
            f"Complete plan should not have missing page warnings: {type_warnings}"


class TestRule8TemplateFileCorrection:
    """Rule 8: Auto-correct create -> modify for template files."""

    def test_layout_create_corrected_to_modify(self):
        plan = [
            {"id": "1", "type": "create", "file_path": "app/layout.tsx",
             "description": "Root layout"},
        ]
        corrected, warnings = validate_plan(plan, {}, {})
        assert corrected[0]["type"] == "modify"
        assert any("create" in w and "modify" in w for w in warnings)

    def test_page_tsx_create_corrected(self):
        plan = [
            {"id": "1", "type": "create", "file_path": "app/page.tsx",
             "description": "Home page"},
        ]
        corrected, warnings = validate_plan(plan, {}, {})
        # Find the task (may have been reordered by Rule 9)
        page_task = next(t for t in corrected if t["file_path"] == "app/page.tsx")
        assert page_task["type"] == "modify"

    def test_new_file_stays_create(self):
        plan = [
            {"id": "1", "type": "create", "file_path": "app/about/page.tsx",
             "description": "About page"},
        ]
        corrected, _ = validate_plan(plan, {}, {})
        about_task = next(t for t in corrected if t["file_path"] == "app/about/page.tsx")
        assert about_task["type"] == "create"


class TestRule9AutoAddDataTs:
    """Rule 9: Auto-add lib/data.ts task if missing."""

    def test_auto_adds_data_ts(self):
        plan = [
            {"id": "1", "type": "modify", "file_path": "app/page.tsx",
             "description": "Home page"},
            {"id": "2", "type": "create", "file_path": "app/about/page.tsx",
             "description": "About page"},
        ]
        corrected, warnings = validate_plan(plan, {}, {})
        data_tasks = [t for t in corrected if t["file_path"] == "lib/data.ts"]
        assert len(data_tasks) == 1, "Should auto-add lib/data.ts task"
        assert data_tasks[0]["type"] == "modify"
        assert any("lib/data.ts" in w for w in warnings)

    def test_does_not_duplicate_data_ts(self):
        plan = [
            {"id": "1", "type": "modify", "file_path": "lib/data.ts",
             "description": "Add sample products"},
            {"id": "2", "type": "create", "file_path": "app/page.tsx",
             "description": "Home page"},
        ]
        corrected, _ = validate_plan(plan, {}, {})
        data_tasks = [t for t in corrected if t["file_path"] == "lib/data.ts"]
        assert len(data_tasks) == 1, "Should not duplicate existing lib/data.ts task"

    def test_single_task_plan_no_auto_add(self):
        """Single-task plans don't need sample data scaffolding."""
        plan = [
            {"id": "1", "type": "modify", "file_path": "app/page.tsx",
             "description": "Update page"},
        ]
        corrected, warnings = validate_plan(plan, {}, {})
        data_warnings = [w for w in warnings if "lib/data.ts" in w]
        assert len(data_warnings) == 0
