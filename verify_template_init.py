
"""
Antigravity Agent - Template Initialization Verification

This script verifies that the agent correctly initializes with the Next.js template
when no existing file system is provided.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from unittest.mock import patch, MagicMock
from agent.graph_logic import create_antigravity_graph
from agent.template_loader import load_template

async def mock_upload_node(state, config):
    print("  [Mock] template_upload_node called")
    return {"build_logs": state.get("build_logs", []) + ["Mock upload success"]}

async def main():
    print("=" * 60)
    print("VERIFICATION: Template Initialization (No Persistence)")
    print("=" * 60)

    # Patch template_upload_node to avoid backend connection
    with patch("agent.template_nodes.template_upload_node", side_effect=mock_upload_node) as mock_upload:

        # 1. Manually load template (simulating what run_antigravity_agent does)
        print("\n[Action] Loading template 'nextjs-app'...")
        try:
            file_system = load_template("nextjs-app")
            print(f"  ✓ Template loaded ({len(file_system)} files)")
        except Exception as e:
            print(f"  ✗ Failed to load template: {e}")
            return False

        # 2. Run agent graph with this file system
        print("\n[Action] Running planner with template context...")
        
        # Minimal manifest
        manifest = {
            "name": "template-test",
            "endpoints": []
        }
        
        # "Do nothing" prompt
        user_prompt = "Just verify the project structure. Do not generate new code yet."
        
        # Create graph WITHOUT persistence (checkpointer=None)
        # This bypasses the REDIS_URL requirement
        graph = create_antigravity_graph(checkpointer=None, skip_approval=False)
        
        initial_state = {
            "manifest": manifest,
            "user_prompt": user_prompt,
            "file_system": file_system, # Pass loaded template
            "template_files": file_system.copy(),
            "selected_template": "nextjs-app",
            "org_slug": "test-org",
            "project_slug": "test-project",
            "implementation_plan": [],
            "build_logs": [],
            "iteration_count": 0,
            "messages": [],
            "approved": False,
            "build_ready": False,
            "build_status": "pending",
        }
        
        try:
            # Run graph - config requires thread_id even without checkpointer for some logic
            config = {"configurable": {"thread_id": "verify-template-1"}}
            
            # Invoke until interrupt (approval)
            # Note: without checkpointer, interrupt might execute differently, 
            # but we just want to see if it accepts the state.
            # Actually, with skip_approval=False and NO checkpointer, interrupt will raise error 
            # saying "checkpointer required for interrupt".
            # So we should use skip_approval=True for this test to let it pass through to generator
            # OR just verify the planner sees the files.
            
            # Let's verify planner sees files. 
            # Planner output is in the state.
            
            # We'll use skip_approval=True effectively simulating "Auto-Approved" 
            # or just run one step.
            graph = create_antigravity_graph(checkpointer=None, skip_approval=True)
            
            # Run!
            result = await graph.ainvoke(initial_state, config=config)
            
            # 3. Check result state
            final_fs = result.get("file_system", {})
            print(f"\n[Check] Final file system contains {len(final_fs)} files.")
            
            required_files = [
                "package.json",
                "tsconfig.json",
                "next.config.js",
                "app/layout.tsx",
                "components/ui/button.tsx",
                "lib/utils.ts",
                "tailwind.config.js"
            ]
            
            missing = []
            for f in required_files:
                if f in final_fs:
                    print(f"  ✓ Found: {f}")
                else:
                    print(f"  ✗ Missing: {f}")
                    missing.append(f)
            
            if not missing:
                print("\n✅ Verification SUCCESS: Template initialized and preserved!")
                return True
            else:
                print(f"\n❌ Verification FAILED: Missing {len(missing)} template files.")
                return False

        except Exception as e:
            print(f"\n❌ Error during verification: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
