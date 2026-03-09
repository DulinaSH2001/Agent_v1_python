"""
Tests for the project type classifier.
"""

import pytest

from agent.project_classifier import (
    classify_project,
    get_relevant_components,
    _score_keywords,
)


class TestClassifyProject:
    """Test project type classification accuracy."""

    @pytest.mark.parametrize("prompt,expected_type", [
        ("Build me a portfolio website", "portfolio"),
        ("Create a personal site to showcase my work", "portfolio"),
        ("I need a developer portfolio with resume", "portfolio"),
        ("Create an ecommerce store", "ecommerce"),
        ("Online shop with cart and checkout", "ecommerce"),
        ("Product catalog with shopping cart", "ecommerce"),
        ("Admin dashboard for managing users", "dashboard"),
        ("Analytics dashboard with charts", "dashboard"),
        ("CRM management panel", "dashboard"),
        ("Personal blog with markdown support", "blog"),
        ("Create a blog for writing articles", "blog"),
        ("Landing page for my SaaS startup", "landing"),
        ("Marketing page with waitlist signup", "landing"),
    ])
    def test_correct_classification(self, prompt, expected_type):
        result = classify_project(prompt)
        assert result["type"] == expected_type, \
            f"Expected '{expected_type}' but got '{result['type']}' for: {prompt}"
        assert result["confidence"] > 0.0

    @pytest.mark.parametrize("prompt", [
        "Build something cool",
        "Make me a website",
        "Hello world",
        "",
    ])
    def test_general_fallback(self, prompt):
        result = classify_project(prompt)
        assert result["type"] == "general"
        assert result["confidence"] == 0.0

    def test_result_structure(self):
        result = classify_project("Build a portfolio")
        assert "type" in result
        assert "config" in result
        assert "confidence" in result
        assert "required_pages" in result["config"]
        assert "layout_style" in result["config"]
        assert "nav_style" in result["config"]

    def test_portfolio_required_pages(self):
        result = classify_project("Build a portfolio website")
        pages = result["config"]["required_pages"]
        assert any("hero" in p or "landing" in p for p in pages)
        assert any("about" in p for p in pages)
        assert any("contact" in p for p in pages)

    def test_ecommerce_required_pages(self):
        result = classify_project("Create an ecommerce store")
        pages = result["config"]["required_pages"]
        assert any("product" in p for p in pages)
        assert any("cart" in p for p in pages)
        assert any("checkout" in p for p in pages)


class TestRelevantComponents:
    """Test project-type-aware component filtering."""

    def test_portfolio_no_sidebar(self):
        comps = get_relevant_components("portfolio")
        assert comps["sidebar"] is False
        assert comps["data_table"] is False
        assert comps["stat_card"] is False

    def test_dashboard_all_components(self):
        comps = get_relevant_components("dashboard")
        assert comps["sidebar"] is True
        assert comps["data_table"] is True
        assert comps["stat_card"] is True

    def test_ecommerce_data_table_only(self):
        comps = get_relevant_components("ecommerce")
        assert comps["data_table"] is True
        assert comps["sidebar"] is False
        assert comps["stat_card"] is False

    def test_general_includes_all(self):
        comps = get_relevant_components("general")
        assert comps["sidebar"] is True
        assert comps["data_table"] is True
        assert comps["stat_card"] is True

    def test_unknown_type_includes_all(self):
        comps = get_relevant_components("nonexistent")
        assert comps["sidebar"] is True


class TestScoreKeywords:
    """Test keyword scoring edge cases."""

    def test_single_keyword_above_threshold(self):
        score = _score_keywords("portfolio website", ["portfolio"])
        assert score >= 0.3, "Single exact keyword should clear threshold"

    def test_no_match_returns_zero(self):
        score = _score_keywords("build something", ["portfolio", "resume"])
        assert score == 0.0

    def test_multiple_matches_higher_score(self):
        single = _score_keywords("portfolio", ["portfolio", "resume", "cv"])
        multi = _score_keywords(
            "portfolio resume cv",
            ["portfolio", "resume", "cv"],
        )
        assert multi > single

    def test_multi_word_keyword_bonus(self):
        single_word = _score_keywords("shop", ["shop"])
        multi_word = _score_keywords(
            "personal site",
            ["personal site"],
        )
        assert multi_word >= single_word
