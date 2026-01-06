
import sys
import os
from pathlib import Path

# Add agent root to path so imports work
current_dir = Path(os.getcwd())
sys.path.append(str(current_dir))

from agent.template_manager import TemplateManager

def test_template_manager():
    print(f"Testing TemplateManager from {current_dir}")
    
    # Initialize manager
    manager = TemplateManager()
    
    print(f"Templates dir: {manager.templates_dir}")
    print(f"Exists: {manager.templates_dir.exists()}")
    
    print(f"\nDiscovered {len(manager.available_templates)} templates:")
    for name, config in manager.available_templates.items():
        print(f"- {name}: Framework={config.framework}, Files={len(config.files)}")
        if len(config.files) == 0:
            print("  WARNING: 0 files found!")
            
    # Test selection
    query = "Create a nextjs app"
    selected = manager.select_template_from_query(query)
    
    if selected:
        print(f"\nQuery '{query}' selected: {selected.name} with {len(selected.files)} files")
    else:
        print(f"\nQuery '{query}' selected NOTHING")

if __name__ == "__main__":
    test_template_manager()
