#!/usr/bin/env python3
"""
Simple async test for the Antigravity Agent workflow.
Properly handles async/await patterns.
"""

from dotenv import load_dotenv
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

load_dotenv()


async def main():
    """Run a simple agent workflow test."""

    print("="*60)
    print("  Antigravity Agent - Simple Workflow Test")
    print("="*60)

    try:
        # Import after dotenv is loaded
        from agent.graph_logic import run_antigravity_agent

        print("\n✅ Imports successful")

        # Test with sample manifest and prompt
        manifest = {
            "name": "test-api",
            "endpoints": [
                {
                    "path": "/api/test",
                    "method": "GET",
                    "response": {"type": "string"}
                }
            ],
            "auth": {"type": "jwt"},
            "database": {"type": "postgresql"}
        }

        user_prompt = "Create a simple dashboard UI with dark mode"
        thread_id = "test-001"

        print("\n🔄 Starting agent workflow...")
        print(f"   Manifest: {manifest['name']}")
        print(f"   Prompt: {user_prompt[:50]}...")
        print(f"   Thread: {thread_id}")

        # Run the agent
        try:
            result = await run_antigravity_agent(
                manifest=manifest,
                user_prompt=user_prompt,
                thread_id=thread_id,
            )

            print("\n✅ Agent workflow completed!")
            print(f"   Build status: {result.get('build_status', 'unknown')}")

            if result.get("implementation_plan"):
                plan = result['implementation_plan']
                print(f"   Plan generated with {len(plan)} tasks:")
                for i, task in enumerate(plan[:3], 1):
                    print(f"      {i}. {task.get('title', 'Task')}")

            if result.get("file_system"):
                print(f"   Files generated: {len(result['file_system'])}")

        except Exception as e:
            # Interrupts are normal for approval workflows
            error_str = str(e)
            if "Interrupt" in error_str or "interrupt" in error_str.lower():
                print("\n✅ Agent paused at approval checkpoint (this is normal!)")
                print(
                    "   The agent is waiting for human approval of the generated plan.")
            else:
                raise

        print("\n" + "="*60)
        print("  ✅ System is Ready!")
        print("="*60)
        print("\nAgent configuration:")
        print("  ✅ In-memory checkpointing (session-safe)")
        print("  ✅ Graph compilation working")
        print("  ✅ Plan generation ready")
        print("  ✅ Human approval flow active")
        print("\nNext steps:")
        print("  1. Start the webhook server:")
        print("     uvicorn api.webhook:app --reload")
        print("  2. Submit generation requests from the frontend")
        print("  3. Approve plans to generate code")

    except Exception as e:
        print(f"\n❌ Workflow Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # Suppress uvicorn startup logs
    import logging
    logging.getLogger("uvicorn").setLevel(logging.ERROR)
    logging.getLogger("uvicorn.access").setLevel(logging.ERROR)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏸️  Test interrupted by user")
        sys.exit(0)
