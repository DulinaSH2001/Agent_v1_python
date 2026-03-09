"""
Antigravity Agent - Simple Workflow Test (No Redis Required)

This script demonstrates the agent workflow without Redis persistence.
Use this for quick testing.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


async def main():
    """Run a simple test without Redis."""
    
    print("=" * 60)
    print("SIMPLE WORKFLOW TEST (No Redis)")
    print("=" * 60)
    
    # Import components directly
    from agent.graph_logic import (
        get_planning_llm,
        create_antigravity_graph,
        ARCHITECT_PROMPT,
    )
    from agent.execution_layer import get_generation_llm, BUILDER_PROMPT
    from langchain_core.messages import HumanMessage, SystemMessage
    import json
    
    # Test manifest
    manifest = {
        "name": "test-app",
        "endpoints": [
            {"path": "/api/users", "method": "GET", "response": {"type": "User[]"}},
        ],
        "auth": {"type": "jwt"},
    }
    
    user_prompt = "Create a simple dashboard page with a user table."
    
    # STEP 1: Test Planning
    print("\n" + "-" * 40)
    print("STEP 1: Testing Planning (Architect)")
    print("-" * 40)
    
    try:
        planning_llm = get_planning_llm()
        print(f"  ✓ LLM: {type(planning_llm).__name__}")
        
        messages = [
            SystemMessage(content=ARCHITECT_PROMPT),
            HumanMessage(content=f"""
## Manifest
{json.dumps(manifest, indent=2)}

## User Request
{user_prompt}

## Instructions
Generate a simple implementation plan with 2-3 tasks.
Return ONLY a JSON array.
""")
        ]
        
        print("  Generating plan...")
        response = await planning_llm.ainvoke(messages)
        content = response.content.strip()
        
        # Parse JSON
        if content.startswith("```"):
            lines = content.split("\n")[1:-1]
            content = "\n".join(lines)
        
        plan = json.loads(content)
        print(f"  ✓ Plan generated: {len(plan)} tasks")
        
        for i, task in enumerate(plan, 1):
            print(f"    {i}. [{task.get('type', 'create')}] {task.get('file_path', 'unknown')}")
        
    except Exception as e:
        print(f"  ✗ Planning failed: {e}")
        return 1
    
    # STEP 2: Test Code Generation
    print("\n" + "-" * 40)
    print("STEP 2: Testing Code Generation (Builder)")
    print("-" * 40)
    
    try:
        gen_llm = get_generation_llm()
        print(f"  ✓ LLM: {type(gen_llm).__name__}")
        
        # Generate code for first task
        first_task = plan[0] if plan else {"file_path": "app/page.tsx", "description": "Create main page"}
        
        messages = [
            SystemMessage(content=BUILDER_PROMPT),
            HumanMessage(content=f"""
## Task
{first_task.get('description', 'Create a simple page')}

## File Path
{first_task.get('file_path', 'app/page.tsx')}

## Manifest
{json.dumps(manifest, indent=2)}

## Instructions
Generate the complete file content. Return ONLY the code.
""")
        ]
        
        print(f"  Generating: {first_task.get('file_path', 'app/page.tsx')}...")
        response = await gen_llm.ainvoke(messages)
        code = response.content.strip()
        
        # Clean markdown
        if code.startswith("```"):
            lines = code.split("\n")[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines)
        
        print(f"  ✓ Generated {len(code)} bytes")
        print("\n  Preview (first 15 lines):")
        print("  " + "-" * 38)
        for line in code.split("\n")[:15]:
            print(f"  | {line}")
        print("  " + "-" * 38)
        
    except Exception as e:
        print(f"  ✗ Generation failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # STEP 3: Test Graph Creation (without checkpoint)
    print("\n" + "-" * 40)
    print("STEP 3: Testing Graph Creation")
    print("-" * 40)
    
    try:
        # Create graph without checkpointer
        graph = create_antigravity_graph(checkpointer=None, enable_reflexion=True)
        nodes = list(graph.nodes.keys())
        print(f"  ✓ Graph created with {len(nodes)} nodes:")
        for node in nodes:
            print(f"    - {node}")
        
    except Exception as e:
        print(f"  ✗ Graph creation failed: {e}")
        return 1
    
    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED!")
    print("=" * 60)
    print("""
Next steps:
1. Fix Redis module issue: pip3 install --upgrade langgraph-checkpoint-redis
2. Or run without persistence for testing
3. See USER_GUIDE.md for full workflow with Redis
""")
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
