"""
Codebase Analysis for Smart Modifications

Analyzes the generated or existing codebase to:
- Build import dependency graphs
- Identify React components and their props
- Find affected files for a modification request
- Generate codebase summaries for LLM context
"""

import re
from typing import Dict, List, Set, Optional, Any
from pathlib import Path
from dataclasses import dataclass, asdict
import json


@dataclass
class PropSchema:
    """Component prop metadata"""
    name: str
    required: bool = True
    type_hint: str = "any"
    description: str = ""


@dataclass
class ComponentInfo:
    """React component metadata"""
    name: str
    file_path: str
    is_client: bool = False
    uses_hooks: bool = False
    props: List[str] = None
    prop_schemas: List[PropSchema] = None
    imports: List[str] = None
    exports: bool = False
    dependencies: List[str] = None

    def __post_init__(self):
        self.props = self.props or []
        self.prop_schemas = self.prop_schemas or []
        self.imports = self.imports or []
        self.dependencies = self.dependencies or []


@dataclass
class ImportGraph:
    """Import dependency relationship"""
    file_path: str
    imports: List[str]
    imported_by: List[str] = None

    def __post_init__(self):
        self.imported_by = self.imported_by or []


class CodebaseAnalyzer:
    """Analyzes TypeScript/JavaScript React codebase structure"""

    # Regex patterns
    COMPONENT_PATTERN = re.compile(
        r'(?:export\s+(?:const|function|default)\s+)?(?P<name>\w+)\s*(?:=|:)?\s*(?:\(|function).*?(?:\{|=>)',
        re.MULTILINE
    )
    USE_CLIENT_PATTERN = re.compile(r"^['\"]use client['\"];?$", re.MULTILINE)
    HOOK_PATTERN = re.compile(r'\b(use\w+)\s*\(')
    IMPORT_PATTERN = re.compile(
        r"^(?:import|from)\s+['\"](?P<path>[^'\"]+)['\"]",
        re.MULTILINE
    )
    EXPORT_PATTERN = re.compile(
        r"^export\s+(?:default\s+)?(?:function|const|class)\s+(\w+)",
        re.MULTILINE
    )
    # Pattern to find interface/type definitions
    INTERFACE_PATTERN = re.compile(
        r"(?:interface|type)\s+(\w+Props?)\s*(?:=\s*)?\{([^}]+)\}",
        re.MULTILINE | re.DOTALL
    )
    # Pattern to find function/component signatures with props
    FUNCTION_SIGNATURE_PATTERN = re.compile(
        r"(?:export\s+)?(?:const|function)\s+(\w+)\s*(?::\s*React\.FC<([^>]+)>|\(\s*(\w+)\s*:\s*([^)]+)\))",
        re.MULTILINE
    )

    def __init__(self):
        self.files: Dict[str, str] = {}  # file_path -> content
        self.import_graph: Dict[str, ImportGraph] = {}
        self.components: Dict[str, ComponentInfo] = {}

    def analyze_files(self, file_system: Dict[str, str]) -> None:
        """
        Analyze a complete file system.
        Args:
            file_system: Dict of {file_path: content}
        """
        self.files = file_system
        self._build_import_graph()
        self._identify_components()

    def _build_import_graph(self) -> None:
        """Build import dependency graph from all files"""
        for file_path, content in self.files.items():
            if not self._is_code_file(file_path):
                continue

            imports = self._extract_imports(content, file_path)
            self.import_graph[file_path] = ImportGraph(
                file_path=file_path,
                imports=imports
            )

        # Build reverse dependencies (imported_by)
        for file_path, graph in self.import_graph.items():
            for imported_path in graph.imports:
                if imported_path in self.import_graph:
                    self.import_graph[imported_path].imported_by.append(file_path)

    def _extract_imports(self, content: str, file_path: str) -> List[str]:
        """Extract all import paths from a file"""
        imports = []
        for match in self.IMPORT_PATTERN.finditer(content):
            import_path = match.group('path')
            # Normalize relative imports
            if import_path.startswith('.'):
                # Resolve relative to current file
                resolved = self._resolve_relative_import(import_path, file_path)
                imports.append(resolved)
            elif import_path.startswith('@/'):
                # Alias import (e.g., @/components)
                imports.append(import_path)
            elif not import_path.startswith('react') and not import_path.startswith('next'):
                # External package
                imports.append(import_path)
        return imports

    def _resolve_relative_import(self, rel_path: str, from_file: str) -> str:
        """Resolve relative import to absolute path"""
        from_dir = str(Path(from_file).parent)
        resolved = str((Path(from_dir) / rel_path).resolve())
        # Normalize to forward slashes
        return resolved.replace('\\', '/')

    def _identify_components(self) -> None:
        """Identify React components in all files"""
        for file_path, content in self.files.items():
            if not self._is_react_file(file_path):
                continue

            is_client = self.USE_CLIENT_PATTERN.search(content) is not None

            # Extract component names
            for match in self.EXPORT_PATTERN.finditer(content):
                component_name = match.group(1)
                uses_hooks = self._detects_hooks(content)
                imports = self._extract_imports(content, file_path)
                dependencies = self.import_graph.get(file_path, ImportGraph(file_path, [])).imports

                # Extract prop schemas for this component
                prop_schemas = self._extract_prop_schemas(content, component_name)

                self.components[component_name] = ComponentInfo(
                    name=component_name,
                    file_path=file_path,
                    is_client=is_client,
                    uses_hooks=uses_hooks,
                    imports=imports,
                    prop_schemas=prop_schemas,
                    dependencies=dependencies,
                    exports=True
                )

    def _detects_hooks(self, content: str) -> bool:
        """Check if code uses React hooks"""
        return bool(self.HOOK_PATTERN.search(content))

    def _extract_prop_schemas(self, content: str, component_name: str) -> List[PropSchema]:
        """Extract prop schemas from component interface definition"""
        # Look for ComponentNameProps interface
        props_interface_name = f"{component_name}Props"

        # Try exact name match first
        interface_pattern = re.compile(
            rf"(?:interface|type)\s+{props_interface_name}\s*(?:=\s*)?\{{([^}}]+)\}}",
            re.MULTILINE | re.DOTALL
        )

        match = interface_pattern.search(content)
        if not match:
            return []

        interface_body = match.group(1)
        schemas = []

        # Parse prop lines
        prop_lines = re.split(r'[;,\n]', interface_body)

        for line in prop_lines:
            line = line.strip()
            if not line or line.startswith('//'):
                continue

            # Parse: propName?: type or propName: type
            prop_match = re.match(r'["\']?(\w+)["\']?(\?)?:\s*(.+?)(?:;|,|$)', line)
            if prop_match:
                prop_name = prop_match.group(1)
                is_optional = bool(prop_match.group(2))
                prop_type = prop_match.group(3).strip()

                schemas.append(PropSchema(
                    name=prop_name,
                    required=not is_optional,
                    type_hint=prop_type[:100],  # Truncate long type hints
                ))

        return schemas

    def _is_code_file(self, file_path: str) -> bool:
        """Check if file is source code"""
        return file_path.endswith(('.ts', '.tsx', '.js', '.jsx'))

    def _is_react_file(self, file_path: str) -> bool:
        """Check if file is a React component (not just any code file)"""
        return file_path.endswith(('.tsx', '.jsx'))

    def find_affected_files(
        self,
        modification_request: str,
        file_system: Dict[str, str]
    ) -> List[str]:
        """
        Find which files are likely affected by a modification request.
        Uses keyword matching on request + dependency analysis.
        """
        self.analyze_files(file_system)

        # Extract keywords from modification request
        keywords = self._extract_keywords(modification_request)

        # Find matching files and components
        affected = set()

        for keyword in keywords:
            # Direct filename matches
            for file_path in self.files.keys():
                if keyword.lower() in file_path.lower():
                    affected.add(file_path)

            # Component name matches
            for comp_name, comp_info in self.components.items():
                if keyword.lower() in comp_name.lower():
                    affected.add(comp_info.file_path)
                    # Also add files that import this component
                    if comp_info.file_path in self.import_graph:
                        affected.update(
                            self.import_graph[comp_info.file_path].imported_by
                        )

        # If no matches found, return most likely candidates (entry points, layouts)
        if not affected:
            affected = self._guess_entry_points()

        return sorted(list(affected))

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract meaningful keywords from text"""
        # Simple keyword extraction: split on word boundaries, remove common words
        common_words = {
            'the', 'a', 'an', 'and', 'or', 'is', 'are', 'was', 'were',
            'please', 'add', 'modify', 'update', 'change', 'make', 'do',
            'to', 'for', 'in', 'on', 'at', 'by', 'with', 'from'
        }
        words = re.findall(r'\b\w+\b', text.lower())
        return [w for w in words if w not in common_words and len(w) > 2]

    def _guess_entry_points(self) -> Set[str]:
        """Guess main entry files if no keyword matches"""
        candidates = set()
        for file_path in self.files.keys():
            # Common entry points
            if any(name in file_path.lower() for name in [
                'app.tsx', 'app.jsx', 'page.tsx', 'page.jsx',
                'index.tsx', 'index.jsx', 'layout.tsx'
            ]):
                candidates.add(file_path)
        return candidates if candidates else set(self.files.keys())

    def get_file_summary(self) -> str:
        """
        Generate a structured summary of the codebase for LLM context.
        Includes file structure, components, and dependencies.
        """
        lines = []
        lines.append("## Codebase Summary")
        lines.append(f"\n### File Structure ({len(self.files)} files)")
        lines.append("```")
        for file_path in sorted(self.files.keys()):
            lines.append(file_path)
        lines.append("```")

        if self.components:
            lines.append(f"\n### React Components ({len(self.components)} components)")
            for name, info in sorted(self.components.items()):
                marker = "🔧" if info.uses_hooks else "📦"
                client_marker = " (client)" if info.is_client else ""
                lines.append(f"{marker} **{name}** ({info.file_path}){client_marker}")
                if info.dependencies:
                    lines.append(f"   - Imports: {', '.join(info.dependencies[:3])}")

        if self.import_graph:
            lines.append(f"\n### Key Dependencies")
            # Find most imported files
            import_counts = {}
            for graph in self.import_graph.values():
                for imp in graph.imports:
                    import_counts[imp] = import_counts.get(imp, 0) + 1
            # Show top 5
            for imp, count in sorted(import_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
                lines.append(f"- {imp} (imported {count}x)")

        return "\n".join(lines)

    def analyze_file_impact(self, file_path: str) -> Dict[str, Any]:
        """
        Analyze impact of modifying a file:
        - What files import it?
        - What components does it define?
        - What does it import?
        """
        if file_path not in self.files:
            return {}

        content = self.files[file_path]
        graph = self.import_graph.get(file_path, ImportGraph(file_path, []))

        return {
            'file_path': file_path,
            'imported_by': graph.imported_by,
            'imports': graph.imports,
            'components': [
                name for name, info in self.components.items()
                if info.file_path == file_path
            ],
            'is_react_file': self._is_react_file(file_path),
            'has_client_directive': self.USE_CLIENT_PATTERN.search(content) is not None,
            'impact_scope': len(graph.imported_by),  # how many files would be affected
        }

    def extract_component_signatures(self) -> Dict[str, Any]:
        """
        Extract component prop signatures for LLM context.

        Returns:
        {
            "Header": {
                "file_path": "components/Header.tsx",
                "required_props": ["links", "branding"],
                "all_props": ["links", "branding", "className"],
                "prop_details": {
                    "links": {"required": True, "type": "Array"},
                    "branding": {"required": True, "type": "Object"}
                }
            },
            ...
        }
        """
        signatures = {}

        for comp_name, comp_info in self.components.items():
            if not comp_info.exports:
                continue

            # Extract prop signatures from the file
            content = self.files.get(comp_info.file_path, "")
            props_info = self._extract_props_from_content(content, comp_name)

            if props_info:
                signatures[comp_name] = {
                    "file_path": comp_info.file_path,
                    "required_props": props_info.get("required", []),
                    "all_props": props_info.get("all", []),
                    "prop_details": props_info.get("details", {}),
                    "is_client": comp_info.is_client,
                }

        return signatures

    def _extract_props_from_content(self, content: str, component_name: str) -> Optional[Dict[str, Any]]:
        """
        Extract prop information from component file content.
        Looks for interface definitions matching ComponentNameProps pattern.
        """
        # Look for interface/type definition: ComponentNameProps
        props_interface_name = f"{component_name}Props"

        # Try to find the interface
        interface_pattern = re.compile(
            rf"(?:interface|type)\s+{props_interface_name}\s*(?:=\s*)?\{{([^}}]+)\}}",
            re.MULTILINE | re.DOTALL
        )

        match = interface_pattern.search(content)
        if not match:
            # Try alternate pattern: component name + "Props"
            interface_pattern = re.compile(
                rf"(?:interface|type)\s+(\w*Props)\s*(?:=\s*)?\{{([^}}]+)\}}",
                re.MULTILINE | re.DOTALL
            )
            match = interface_pattern.search(content)
            if not match:
                return None

        # Extract interface body
        interface_body = match.group(1) if match.lastindex >= 1 else ""

        # Parse prop names and types
        required_props = []
        all_props = []
        prop_details = {}

        # Split by semicolon or comma
        prop_lines = re.split(r'[;,\n]', interface_body)

        for line in prop_lines:
            line = line.strip()
            if not line or line.startswith('//'):
                continue

            # Extract prop name and type
            # Format: "propName": "type" or "propName?": "type" or propName: type
            prop_match = re.match(r'["\']?(\w+)["\']?\??:\s*(.+?)(?:;|,|$)', line)
            if prop_match:
                prop_name = prop_match.group(1)
                prop_type = prop_match.group(2).strip()
                is_required = '?' not in line[:line.find(':')]

                all_props.append(prop_name)
                if is_required:
                    required_props.append(prop_name)

                prop_details[prop_name] = {
                    "required": is_required,
                    "type": prop_type[:50],  # Truncate long types
                }

        return {
            "required": required_props,
            "all": all_props,
            "details": prop_details,
        } if all_props else None

    def get_component_signature_string(self) -> str:
        """
        Generate a formatted string of all component signatures for LLM context.
        """
        signatures = self.extract_component_signatures()

        if not signatures:
            return "No custom components found in the project."

        lines = ["## Available Components in Your Project\n"]

        for comp_name, sig_data in sorted(signatures.items()):
            lines.append(f"### {comp_name}")
            lines.append(f"File: `{sig_data['file_path']}`")

            if sig_data.get('required_props'):
                lines.append(f"**Required props**: {', '.join(sig_data['required_props'])}")

            if sig_data.get('all_props'):
                lines.append(f"**All props**: {', '.join(sig_data['all_props'])}")

            if sig_data.get('prop_details'):
                lines.append("\nProp details:")
                for prop_name, prop_info in sig_data['prop_details'].items():
                    req_marker = " (required)" if prop_info.get('required') else " (optional)"
                    lines.append(f"- `{prop_name}`: {prop_info.get('type', 'any')}{req_marker}")

            lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize analyzer state to dict"""
        return {
            'components': {
                name: asdict(info)
                for name, info in self.components.items()
            },
            'import_graph': {
                path: asdict(graph)
                for path, graph in self.import_graph.items()
            },
            'component_signatures': self.extract_component_signatures(),
        }
