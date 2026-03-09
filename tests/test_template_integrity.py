"""
Tests for template integrity — ensures the CSS pipeline, project neutrality,
and file structure are correct.
"""

import json
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "nextjs-app"


class TestCSSPipeline:
    """Verify the complete Tailwind CSS pipeline is configured."""

    def test_postcss_config_exists(self):
        path = TEMPLATE_DIR / "postcss.config.js"
        assert path.exists(), "postcss.config.js is missing — Tailwind CSS will not work"

    def test_postcss_config_has_tailwind_plugin(self):
        content = (TEMPLATE_DIR / "postcss.config.js").read_text()
        assert "tailwindcss" in content, "postcss.config.js must reference tailwindcss plugin"
        assert "autoprefixer" in content, "postcss.config.js must reference autoprefixer plugin"

    def test_tailwind_config_exists(self):
        assert (TEMPLATE_DIR / "tailwind.config.js").exists()

    def test_tailwind_config_has_content_paths(self):
        content = (TEMPLATE_DIR / "tailwind.config.js").read_text()
        assert "components/**" in content, "tailwind.config.js must scan components/"
        assert "app/**" in content, "tailwind.config.js must scan app/"

    def test_globals_css_has_tailwind_directives(self):
        content = (TEMPLATE_DIR / "styles" / "globals.css").read_text()
        assert "@tailwind base" in content
        assert "@tailwind components" in content
        assert "@tailwind utilities" in content

    def test_layout_imports_globals_css(self):
        content = (TEMPLATE_DIR / "app" / "layout.tsx").read_text()
        assert "globals.css" in content, "app/layout.tsx must import globals.css"

    def test_package_json_has_css_deps(self):
        pkg = json.loads((TEMPLATE_DIR / "package.json").read_text())
        dev_deps = pkg.get("devDependencies", {})
        assert "postcss" in dev_deps, "postcss must be in devDependencies"
        assert "tailwindcss" in dev_deps, "tailwindcss must be in devDependencies"
        assert "autoprefixer" in dev_deps, "autoprefixer must be in devDependencies"


class TestTemplateNeutrality:
    """Ensure the template does not bias generation toward any project type."""

    def test_no_orders_table(self):
        assert not (TEMPLATE_DIR / "app" / "orders-table.tsx").exists(), \
            "orders-table.tsx should not exist in template (ecommerce bias)"

    def test_page_tsx_is_neutral(self):
        content = (TEMPLATE_DIR / "app" / "page.tsx").read_text()
        # Should NOT contain ecommerce or dashboard content
        assert "OrdersTable" not in content
        assert "StatCard" not in content
        assert "sampleOrders" not in content
        assert "Dashboard" not in content
        assert "Revenue" not in content

    def test_data_ts_has_minimal_navlinks(self):
        content = (TEMPLATE_DIR / "lib" / "data.ts").read_text()
        assert '"Home"' in content
        # Should NOT have dashboard-specific nav links
        assert '"Analytics"' not in content
        assert '"Users"' not in content


class TestTemplateStructure:
    """Verify essential template files exist."""

    @pytest.mark.parametrize("file_path", [
        "package.json",
        "tsconfig.json",
        "next.config.js",
        "tailwind.config.js",
        "postcss.config.js",
        "styles/globals.css",
        "app/layout.tsx",
        "app/page.tsx",
        "lib/utils.ts",
        "lib/data.ts",
        "lib/actions.ts",
        "types/index.ts",
        "components/theme-provider.tsx",
    ])
    def test_essential_file_exists(self, file_path):
        assert (TEMPLATE_DIR / file_path).exists(), f"Missing essential file: {file_path}"

    @pytest.mark.parametrize("component", [
        "button", "card", "input", "label", "table",
        "dialog", "badge", "skeleton", "form",
    ])
    def test_shadcn_component_exists(self, component):
        assert (TEMPLATE_DIR / "components" / "ui" / f"{component}.tsx").exists(), \
            f"Missing Shadcn component: {component}"
