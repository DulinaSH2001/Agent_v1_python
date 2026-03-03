"""
Antigravity Agent - Full Workflow Test

This script demonstrates the complete agent workflow:
1. Start with a manifest and user prompt
2. Generate an implementation plan
3. Approve the plan
4. Generate code files
5. (Optional) Handle build results via webhook
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


async def main():
    """Run the full Antigravity agent workflow."""
    
    # Import after dotenv is loaded
    from agent import run_antigravity_agent, resume_antigravity_agent, get_agent_state
    
    # Sample manifest (describes your backend API)
    manifest = {
        "name": "my-app",
        "endpoints": [
            {
                "path": "/api/users",
                "method": "GET",
                "response": {"type": "User[]"}
            },
            {
                "path": "/api/users",
                "method": "POST",
                "body": {"type": "CreateUserInput"},
                "response": {"type": "User"}
            }
        ],
        "auth": {
            "type": "jwt",
            "provider": "clerk"
        },
        "database": {
            "type": "postgresql",
            "orm": "prisma"
        }
    }
    
    user_prompt = """
    Create a modern user management dashboard with:
    - Dark mode design
    - User list table with search
    - Add user form with validation
    - User profile cards
    Use Shadcn UI components.
    """
    
    thread_id = "test-session-001"
    
    print("=" * 60)
    print("STEP 1: Starting Planning Phase")
    print("=" * 60)
    
    try:
        result = await run_antigravity_agent(
            manifest=manifest,
            user_prompt=user_prompt,
            thread_id=thread_id,
        )
        
        # The agent will pause at approval_node
        print("\n📋 Implementation Plan Generated!")
        plan = result.get("implementation_plan", [])
        print(f"   Tasks: {len(plan)}")
        for i, task in enumerate(plan[:5], 1):  # Show first 5
            print(f"   {i}. [{task.get('type', 'N/A')}] {task.get('file_path', 'N/A')}")
            desc = task.get('description', '')[:60]
            print(f"      {desc}...")
        
        if len(plan) > 5:
            print(f"   ... and {len(plan) - 5} more tasks")
        
        print("\n" + "=" * 60)
        print("STEP 2: Approving Plan")
        print("=" * 60)
        
        # Approve the plan
        result = await resume_antigravity_agent(
            thread_id=thread_id,
            action="APPROVE",
        )
        
        print("\n✅ Plan approved! Code generation completed.")
        
        # Check generated files
        file_system = result.get("file_system", {})
        print(f"\n📦 Generated {len(file_system)} files:")
        for path in list(file_system.keys())[:10]:  # Show first 10
            content = file_system[path]
            print(f"   - {path} ({len(content)} bytes)")
        
        if len(file_system) > 10:
            print(f"   ... and {len(file_system) - 10} more files")
        
        # Show build logs
        build_logs = result.get("build_logs", [])
        if build_logs:
            print("\n📜 Build Logs:")
            for log in build_logs[-5:]:  # Last 5 logs
                print(f"   {log}")
        
        # Check build status
        build_ready = result.get("build_ready", False)
        build_status = result.get("build_status", "pending")
        
        print("\n" + "=" * 60)
        print("RESULT")
        print("=" * 60)
        print(f"   Build Ready: {build_ready}")
        print(f"   Build Status: {build_status}")
        print(f"   Files Generated: {len(file_system)}")
        
        if build_status == "pending":
            print("\n💡 Tip: The agent is waiting for a build status webhook.")
            print(f"   Send a POST request to: http://localhost:8000/callbacks/build-status/{thread_id}")
            print('   Body: {"status": "success", "logs": ["Build completed"]}')
        
        # Show a sample file
        if file_system:
            sample_path = next(iter(file_system.keys()))
            sample_content = file_system[sample_path]
            print(f"\n📄 Sample File: {sample_path}")
            print("-" * 40)
            lines = sample_content.split("\n")[:20]
            for line in lines:
                print(f"   {line}")
            if len(sample_content.split("\n")) > 20:
                print("   ...")
        
        print("\n✅ Full workflow test completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
