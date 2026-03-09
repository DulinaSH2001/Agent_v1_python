"""Tests for file operation classification and validation."""

import pytest
from agent.file_ops import (
    PROTECTED_FILES,
    classify_file_operation,
    validate_plan_operations,
)


class TestClassifyFileOperation:
    """Test individual file operation classification."""

    def test_modify_nonexistent_reclassified_to_create(self):
        """Modify on missing file should be reclassified to create."""
        task = {"type": "modify", "file_path": "app/dashboard/page.tsx"}
        file_system = {}

        corrected, warnings = classify_file_operation(task, file_system)
        assert corrected["type"] == "create"
        assert len(warnings) == 1
        assert "modify -> create" in warnings[0]

    def test_create_existing_reclassified_to_modify(self):
        """Create on existing file should be reclassified to modify."""
        task = {"type": "create", "file_path": "app/dashboard/page.tsx"}
        file_system = {"app/dashboard/page.tsx": "existing content"}

        corrected, warnings = classify_file_operation(task, file_system)
        assert corrected["type"] == "modify"
        assert len(warnings) == 1
        assert "create -> modify" in warnings[0]

    def test_delete_nonexistent_skipped(self):
        """Delete on missing file should be marked as skip."""
        task = {"type": "delete", "file_path": "components/old.tsx"}
        file_system = {}

        corrected, warnings = classify_file_operation(task, file_system)
        assert corrected.get("_skip") is True
        assert len(warnings) == 1

    def test_valid_create_unchanged(self):
        """Valid create operation should not be modified."""
        task = {"type": "create", "file_path": "app/new/page.tsx"}
        file_system = {}

        corrected, warnings = classify_file_operation(task, file_system)
        assert corrected["type"] == "create"
        assert len(warnings) == 0

    def test_valid_modify_unchanged(self):
        """Valid modify operation should not be modified."""
        task = {"type": "modify", "file_path": "app/existing/page.tsx"}
        file_system = {"app/existing/page.tsx": "code"}

        corrected, warnings = classify_file_operation(task, file_system)
        assert corrected["type"] == "modify"
        assert len(warnings) == 0

    def test_no_file_path_unchanged(self):
        """Tasks without file_path should pass through."""
        task = {"type": "create", "file_path": ""}
        corrected, warnings = classify_file_operation(task, {})
        assert len(warnings) == 0

    def test_does_not_mutate_original(self):
        """classify_file_operation should not mutate the original task dict."""
        task = {"type": "modify", "file_path": "new.tsx"}
        classify_file_operation(task, {})
        assert task["type"] == "modify"  # Original unchanged


class TestProtectedFiles:
    """Test protected file enforcement."""

    @pytest.mark.parametrize("protected_file", list(PROTECTED_FILES))
    def test_all_protected_files_blocked(self, protected_file):
        """All protected files should be blocked."""
        task = {"type": "modify", "file_path": protected_file}
        corrected, warnings = classify_file_operation(task, {})
        assert corrected.get("_blocked") is True
        assert any("BLOCKED" in w for w in warnings)

    def test_non_protected_file_not_blocked(self):
        """Non-protected files should not be blocked."""
        task = {"type": "modify", "file_path": "app/dashboard/page.tsx"}
        file_system = {"app/dashboard/page.tsx": "code"}
        corrected, warnings = classify_file_operation(task, file_system)
        assert corrected.get("_blocked") is None


class TestValidatePlanOperations:
    """Test full plan validation."""

    def test_mixed_operations(self):
        """Plan with mixed valid/invalid operations should be corrected."""
        plan = [
            {"id": "1", "type": "create", "file_path": "app/new/page.tsx"},
            {"id": "2", "type": "modify", "file_path": "app/nonexistent.tsx"},
            {"id": "3", "type": "modify", "file_path": "app/layout.tsx"},  # protected
            {"id": "4", "type": "delete", "file_path": "app/old.tsx"},  # nonexistent
        ]
        file_system = {}

        corrected, warnings = validate_plan_operations(plan, file_system)
        # Task 1: kept as-is (valid create)
        # Task 2: reclassified to create
        # Task 3: blocked (protected)
        # Task 4: skipped (delete nonexistent)
        assert len(corrected) == 2
        assert corrected[0]["id"] == "1"
        assert corrected[1]["id"] == "2"
        assert corrected[1]["type"] == "create"
        assert len(warnings) == 3

    def test_empty_plan(self):
        """Empty plan should return empty results."""
        corrected, warnings = validate_plan_operations([], {})
        assert corrected == []
        assert warnings == []
