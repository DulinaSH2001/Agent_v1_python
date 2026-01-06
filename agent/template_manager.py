"""
Antigravity Agent - Template Manager

This module manages code generation templates (Next.js, Vite, etc.) for the Antigravity agent.
Provides template selection, loading, and validation capabilities.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TemplateConfig:
    """Configuration for a code generation template."""
    
    name: str
    framework: str  # nextjs, vite, react, etc.
    language: str  # typescript, javascript
    styling: str  # tailwind, css, styled-components
    template_path: Path
    files: Dict[str, str]  # file_path -> file_content
    metadata: Dict  # Additional template metadata


class TemplateManager:
    """
    Manages code generation templates.
    
    Handles template discovery, loading, and selection logic
    for initializing new projects with appropriate scaffolding.
    """
    
    def __init__(self, templates_dir: Optional[str] = None):
        """
        Initialize the template manager.
        
        Args:
            templates_dir: Path to templates directory (defaults to ./templates)
        """
        if templates_dir:
            self.templates_dir = Path(templates_dir)
        else:
            # Default to templates/ in the same directory as Agent root
            agent_root = Path(__file__).parent.parent
            self.templates_dir = agent_root / "templates"
        
        self.available_templates: Dict[str, TemplateConfig] = {}
        self._discover_templates()
    
    def _discover_templates(self) -> None:
        """Discover all available templates in the templates directory."""
        if not self.templates_dir.exists():
            logger.warning(f"Templates directory not found: {self.templates_dir}")
            return
        
        for template_dir in self.templates_dir.iterdir():
            if not template_dir.is_dir():
                continue
            
            # Check for template.json metadata file
            metadata_file = template_dir / "template.json"
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                    
                    template_name = template_dir.name
                    logger.info(f"Discovered template: {template_name}")
                    
                    # Load template files
                    files = self._load_template_files(template_dir)
                    
                    config = TemplateConfig(
                        name=template_name,
                        framework=metadata.get("framework", "unknown"),
                        language=metadata.get("language", "typescript"),
                        styling=metadata.get("styling", "tailwind"),
                        template_path=template_dir,
                        files=files,
                        metadata=metadata
                    )
                    
                    self.available_templates[template_name] = config
                    
                except Exception as e:
                    logger.error(f"Failed to load template {template_dir.name}: {e}")
            else:
                # Fallback: treat directory as basic template without metadata
                template_name = template_dir.name
                files = self._load_template_files(template_dir)
                
                config = TemplateConfig(
                    name=template_name,
                    framework="nextjs" if "nextjs" in template_name else "unknown",
                    language="typescript",
                    styling="tailwind",
                    template_path=template_dir,
                    files=files,
                    metadata={}
                )
                
                self.available_templates[template_name] = config
                logger.info(f"Loaded basic template: {template_name} ({len(files)} files)")
    
    def _load_template_files(self, template_dir: Path) -> Dict[str, str]:
        """
        Load all files from a template directory.
        
        Args:
            template_dir: Path to template directory
            
        Returns:
            Dict mapping relative file paths to file contents
        """
        files = {}
        
        for file_path in template_dir.rglob("*"):
            if file_path.is_file() and file_path.name != "template.json":
                # Get relative path from template root
                rel_path = file_path.relative_to(template_dir)
                rel_path_str = str(rel_path).replace("\\", "/")
                
                try:
                    # Try to read as text
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    files[rel_path_str] = content
                except UnicodeDecodeError:
                    # Skip binary files
                    logger.debug(f"Skipping binary file: {rel_path_str}")
                except Exception as e:
                    logger.warning(f"Failed to read {rel_path_str}: {e}")
        
        return files
    
    def list_templates(self) -> List[Dict]:
        """
        List all available templates with their metadata.
        
        Returns:
            List of template info dicts
        """
        return [
            {
                "name": config.name,
                "framework": config.framework,
                "language": config.language,
                "styling": config.styling,
                "file_count": len(config.files),
                "description": config.metadata.get("description", "")
            }
            for config in self.available_templates.values()
        ]
    
    def get_template(
        self,
        framework: str = "nextjs",
        language: str = "typescript",
        styling: str = "tailwind"
    ) -> Optional[TemplateConfig]:
        """
        Get a template matching the specified criteria.
        
        Args:
            framework: Framework type (nextjs, vite, etc.)
            language: Programming language (typescript, javascript)
            styling: Styling solution (tailwind, css, etc.)
            
        Returns:
            TemplateConfig if match found, None otherwise
        """
        # First try exact match
        for template in self.available_templates.values():
            if (template.framework.lower() == framework.lower() and
                template.language.lower() == language.lower() and
                template.styling.lower() == styling.lower()):
                logger.info(f"Selected template: {template.name}")
                return template
        
        # Fallback: match framework and language only
        for template in self.available_templates.values():
            if (template.framework.lower() == framework.lower() and
                template.language.lower() == language.lower()):
                logger.info(f"Partial match template: {template.name}")
                return template
        
        # Fallback: match framework only
        for template in self.available_templates.values():
            if template.framework.lower() == framework.lower():
                logger.info(f"Framework match template: {template.name}")
                return template
        
        # Last resort: return first available template
        if self.available_templates:
            first_template = next(iter(self.available_templates.values()))
            logger.warning(f"Using fallback template: {first_template.name}")
            return first_template
        
        logger.error("No templates available!")
        return None
    
    def select_template_from_query(self, user_query: str) -> Optional[TemplateConfig]:
        """
        Intelligently select a template based on user's query.
        
        Args:
            user_query: User's generation request
            
        Returns:
            Selected TemplateConfig or None
        """
        query_lower = user_query.lower()
        
        # Detect framework
        framework = "nextjs"  # default
        if "vite" in query_lower or "cra" in query_lower:
            framework = "vite"
        elif "nextjs" in query_lower or "next.js" in query_lower or "next" in query_lower:
            framework = "nextjs"
        
        # Detect language
        language = "typescript"  # default
        if "javascript" in query_lower or "js" in query_lower:
            language = "javascript"
        
        # Detect styling
        styling = "tailwind"  # default
        if "css modules" in query_lower or "css" in query_lower:
            styling = "css"
        elif "styled-components" in query_lower or "styled" in query_lower:
            styling = "styled-components"
        
        logger.info(f"Detected from query - Framework: {framework}, Language: {language}, Styling: {styling}")
        
        return self.get_template(framework=framework, language=language, styling=styling)


# Global template manager instance
_template_manager: Optional[TemplateManager] = None


def get_template_manager() -> TemplateManager:
    """Get or create the global template manager instance."""
    global _template_manager
    if _template_manager is None:
        _template_manager = TemplateManager()
    return _template_manager
