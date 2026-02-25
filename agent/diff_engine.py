"""
Diff Engine for Smart Code Modifications

Generates and applies unified diffs for targeted code updates.
Falls back to full-file replacement when diff application fails.
"""

import difflib
from typing import Tuple, Optional, Dict
import re


class DiffGenerator:
    """Generate and apply unified diffs between code versions"""

    @staticmethod
    def generate_diff(
        original: str,
        modified: str,
        file_path: str = "file",
        context_lines: int = 3
    ) -> str:
        """
        Generate unified diff between original and modified content.
        Returns empty string if no changes.
        """
        if original == modified:
            return ""

        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm='',
            n=context_lines
        )

        return '\n'.join(diff)

    @staticmethod
    def apply_diff(original: str, diff: str) -> Tuple[bool, str]:
        """
        Apply a unified diff to original content.
        Returns (success, result_content).
        If diff fails, returns (False, original) — caller should handle fallback.
        """
        try:
            # Parse diff into lines
            diff_lines = diff.splitlines(keepends=True)
            if not diff_lines:
                return True, original

            # Apply patch using a simple line-by-line approach
            result_lines = []
            original_lines = original.splitlines(keepends=True)

            line_idx = 0
            patch_idx = 0

            while patch_idx < len(diff_lines):
                patch_line = diff_lines[patch_idx]

                # Skip header lines
                if patch_line.startswith(('---', '+++', '@@')):
                    patch_idx += 1
                    continue

                # Context line (should match)
                if patch_line.startswith(' '):
                    expected = patch_line[1:]
                    if line_idx < len(original_lines):
                        actual = original_lines[line_idx]
                        if actual == expected or actual.rstrip() == expected.rstrip():
                            result_lines.append(actual)
                            line_idx += 1
                        else:
                            # Mismatch — diff doesn't apply
                            return False, original
                    patch_idx += 1

                # Deletion
                elif patch_line.startswith('-'):
                    if line_idx < len(original_lines):
                        line_idx += 1
                    patch_idx += 1

                # Addition
                elif patch_line.startswith('+'):
                    result_lines.append(patch_line[1:])
                    patch_idx += 1

                else:
                    patch_idx += 1

            # Append remaining original lines
            while line_idx < len(original_lines):
                result_lines.append(original_lines[line_idx])
                line_idx += 1

            result = ''.join(result_lines)
            return True, result

        except Exception as e:
            # Diff application failed
            return False, original

    @staticmethod
    def apply_diff_with_fallback(
        original: str,
        diff: str,
        modified_full: str
    ) -> str:
        """
        Apply diff to original. If diff fails, fall back to full content.
        Always returns the resulting content (either patched or full replacement).
        """
        success, result = DiffGenerator.apply_diff(original, diff)
        if success:
            return result
        # Fallback to full replacement
        return modified_full

    @staticmethod
    def detect_diff_type(diff: str) -> str:
        """Classify diff type"""
        if not diff:
            return "no_change"

        additions = sum(1 for line in diff.splitlines() if line.startswith('+'))
        deletions = sum(1 for line in diff.splitlines() if line.startswith('-'))

        total = additions + deletions
        if total == 0:
            return "no_change"

        add_ratio = additions / total if total > 0 else 0
        if add_ratio > 0.8:
            return "mostly_additions"
        elif add_ratio < 0.2:
            return "mostly_deletions"
        else:
            return "modifications"

    @staticmethod
    def get_diff_stats(diff: str) -> Dict[str, int]:
        """Extract statistics from diff"""
        lines = diff.splitlines()
        stats = {
            'total_changes': 0,
            'additions': sum(1 for line in lines if line.startswith('+')),
            'deletions': sum(1 for line in lines if line.startswith('-')),
            'lines': len([l for l in lines if l.startswith(('@@ ', '---', '+++'))]),
        }
        stats['total_changes'] = stats['additions'] + stats['deletions']
        return stats

    @staticmethod
    def format_diff_for_display(diff: str, max_lines: int = 50) -> str:
        """Format diff for readable display, truncating if needed"""
        lines = diff.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines)"]
        return '\n'.join(lines)


class TargetedModifier:
    """Apply targeted modifications to specific sections of code"""

    @staticmethod
    def modify_import_section(
        content: str,
        new_imports: Dict[str, str]
    ) -> str:
        """
        Add or update imports at the top of the file.
        new_imports: {'module_path': 'import statement'} or {'Component': 'from module import Component'}
        """
        lines = content.splitlines(keepends=True)

        # Find last import line
        last_import_idx = -1
        for i, line in enumerate(lines):
            if line.lstrip().startswith(('import ', 'from ')):
                last_import_idx = i

        if last_import_idx == -1:
            # No imports yet, add at top (after use client directive if present)
            insert_idx = 0
            if lines and lines[0].strip().startswith(('use client', 'use server')):
                insert_idx = 1

            for import_stmt in new_imports.values():
                lines.insert(insert_idx, import_stmt + '\n')
                insert_idx += 1
        else:
            # Insert after last import
            for import_stmt in reversed(new_imports.values()):
                lines.insert(last_import_idx + 1, import_stmt + '\n')

        return ''.join(lines)

    @staticmethod
    def modify_component_props(
        content: str,
        component_name: str,
        new_props: Dict[str, str]
    ) -> str:
        """
        Update a component's interface/type definition.
        new_props: {'propName': 'PropType'} or {'propName': 'type'}
        """
        # Find interface definition for component
        interface_pattern = rf"interface\s+{component_name}Props\s*\{{([^}}]+)}}"
        match = re.search(interface_pattern, content, re.DOTALL)

        if not match:
            # No existing interface, add one
            component_pattern = rf"(export\s+(?:function|const)\s+{component_name})"
            insert_point = re.search(component_pattern, content)
            if insert_point:
                interface_def = f"\ninterface {component_name}Props {{\n"
                for prop, prop_type in new_props.items():
                    interface_def += f"  {prop}: {prop_type};\n"
                interface_def += "}\n"
                insert_idx = insert_point.start()
                content = content[:insert_idx] + interface_def + content[insert_idx:]
            return content

        # Update existing interface
        old_interface = match.group(0)
        interface_body = match.group(1)

        # Add new props
        new_body = interface_body.rstrip()
        for prop, prop_type in new_props.items():
            # Avoid duplicates
            if f"{prop}:" not in new_body:
                new_body += f"\n  {prop}: {prop_type};"
        new_body += "\n"

        new_interface = f"interface {component_name}Props {{\n{new_body}}}"
        return content.replace(old_interface, new_interface)

    @staticmethod
    def add_use_client_directive(content: str) -> str:
        """Add 'use client' directive at the top of file if not present"""
        if "use client" in content:
            return content

        first_line = content.split('\n', 1)[0]
        if first_line.startswith("'") or first_line.startswith('"'):
            # Already has a directive at top
            lines = content.splitlines(keepends=True)
            lines.insert(1, "'use client';\n\n")
            return ''.join(lines)
        else:
            return "'use client';\n\n" + content
