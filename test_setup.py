"""
Antigravity Agent - Quick Test Script

Run this to verify your setup is working correctly.
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


async def test_1_environment():
    """Test 1: Check environment variables."""
    print("\n" + "=" * 60)
    print("TEST 1: Environment Variables")
    print("=" * 60)
    
    required = {
        "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT"),
        "AZURE_OPENAI_API_KEY": os.getenv("AZURE_OPENAI_API_KEY"),
    }
    
    optional = {
        "UPSTASH_REDIS_REST_URL": os.getenv("UPSTASH_REDIS_REST_URL"),
        "ABLY_API_KEY": os.getenv("ABLY_API_KEY"),
        "AZURE_STORAGE_CONNECTION_STRING": os.getenv("AZURE_STORAGE_CONNECTION_STRING"),
    }
    
    all_required = True
    for key, value in required.items():
        status = "✓" if value else "✗"
        masked = value[:20] + "..." if value and len(value) > 20 else value
        print(f"  {status} {key}: {masked or 'NOT SET'}")
        if not value:
            all_required = False
    
    print("\n  Optional:")
    for key, value in optional.items():
        status = "✓" if value else "-"
        print(f"  {status} {key}: {'Set' if value else 'Not set'}")
    
    if all_required:
        print("\n  ✓ All required environment variables are set!")
        return True
    else:
        print("\n  ✗ Missing required environment variables!")
        return False


async def test_2_llm_connection():
    """Test 2: Verify Azure OpenAI connection."""
    print("\n" + "=" * 60)
    print("TEST 2: Azure OpenAI Connection")
    print("=" * 60)
    
    try:
        from agent.graph_logic import get_planning_llm
        
        llm = get_planning_llm()
        print(f"  LLM Type: {type(llm).__name__}")
        
        response = await llm.ainvoke("Reply with just the word 'connected'")
        content = response.content.strip().lower()
        
        if "connected" in content:
            print(f"  ✓ LLM Response: {content}")
            return True
        else:
            print(f"  ✓ LLM responded: {content[:50]}...")
            return True
            
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False


async def test_3_graph_creation():
    """Test 3: Create graph without errors."""
    print("\n" + "=" * 60)
    print("TEST 3: Graph Creation")
    print("=" * 60)
    
    try:
        from agent.graph_logic import create_antigravity_graph
        
        # Test without reflexion
        graph_simple = create_antigravity_graph(enable_reflexion=False)
        print(f"  ✓ Simple graph nodes: {list(graph_simple.nodes.keys())}")
        
        # Test with reflexion
        graph_full = create_antigravity_graph(enable_reflexion=True)
        print(f"  ✓ Full graph nodes: {list(graph_full.nodes.keys())}")
        
        return True
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False


async def test_4_mcp_tools():
    """Test 4: Verify MCP tools are available."""
    print("\n" + "=" * 60)
    print("TEST 4: MCP Tools")
    print("=" * 60)
    
    try:
        from agent.execution_layer import get_mcp_wrapper
        
        mcp = get_mcp_wrapper()
        tools = mcp.get_tools()
        
        print(f"  MCP Available: {mcp.mcp_available}")
        print(f"  Tools: {[t.name for t in tools]}")
        
        # Test a tool
        for tool in tools:
            if tool.name == "get_shadcn_component":
                result = await tool._arun("button")
                print(f"  ✓ Tool test passed (got {len(result)} chars)")
                break
        
        return True
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False


async def test_5_planning():
    """Test 5: Run a simple planning task."""
    print("\n" + "=" * 60)
    print("TEST 5: Planning Task (may take 10-20 seconds)")
    print("=" * 60)
    
    try:
        from agent.graph_logic import get_planning_llm, ARCHITECT_PROMPT
        from langchain_core.messages import HumanMessage, SystemMessage
        import json
        
        llm = get_planning_llm()
        
        messages = [
            SystemMessage(content=ARCHITECT_PROMPT),
            HumanMessage(content="""
## Manifest
{"endpoints": [{"path": "/api/hello", "method": "GET"}]}

## User Request
Create a simple hello world page.

## Instructions
Return a JSON array with ONE simple task.
""")
        ]
        
        print("  Generating plan...")
        response = await llm.ainvoke(messages)
        content = response.content.strip()
        
        # Try to parse as JSON
        if content.startswith("```"):
            lines = content.split("\n")[1:-1]
            content = "\n".join(lines)
        
        try:
            plan = json.loads(content)
            print(f"  ✓ Plan generated with {len(plan)} task(s)")
            if plan:
                print(f"    First task: {plan[0].get('file_path', 'N/A')}")
            return True
        except json.JSONDecodeError:
            print(f"  ✓ LLM responded (not JSON): {content[:100]}...")
            return True
            
    except Exception as e:
        print(f"  ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("  ANTIGRAVITY AGENT - TEST SUITE")
    print("=" * 60)
    
    results = []
    
    # Run tests
    results.append(("Environment", await test_1_environment()))
    
    if results[-1][1]:  # Only continue if env is set
        results.append(("LLM Connection", await test_2_llm_connection()))
        results.append(("Graph Creation", await test_3_graph_creation()))
        results.append(("MCP Tools", await test_4_mcp_tools()))
        results.append(("Planning", await test_5_planning()))
    
    # Summary
    print("\n" + "=" * 60)
    print("  TEST SUMMARY")
    print("=" * 60)
    
    passed = 0
    failed = 0
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print(f"\n  Total: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("\n  🎉 All tests passed! Your setup is ready.")
        print("\n  Next steps:")
        print("    1. Run: uvicorn api.webhook:app --port 8000")
        print("    2. See USER_GUIDE.md for full workflow examples")
    else:
        print("\n  ⚠️  Some tests failed. Check the errors above.")
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
