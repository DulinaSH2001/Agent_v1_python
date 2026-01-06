
import asyncio
import os
import shutil
import logging
import json
from unittest.mock import MagicMock, patch
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load env variables (for LLM keys etc)
load_dotenv()

# Define test paths
TEST_OUTPUT_DIR = "generated_project"
ORG_SLUG = "test-org"
PROJECT_SLUG = "test-project"

async def mock_upload_to_backend(org_slug, project_slug, files):
    """Mock upload function that writes to local disk"""
    logger.info(f"[MOCK] Uploading {len(files)} files to {TEST_OUTPUT_DIR}...")
    
    os.makedirs(TEST_OUTPUT_DIR, exist_ok=True)
    
    success_count = 0
    results = []
    
    for file_data in files:
        path = file_data["path"]
        content = file_data["content"]
        
        # Determine full path
        full_path = os.path.join(TEST_OUTPUT_DIR, path)
        
        # Create directory if needed
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            success_count += 1
            results.append({"path": path, "success": True})
            logger.info(f"  - Written: {path}")
        except Exception as e:
            results.append({"path": path, "success": False, "error": str(e)})
            logger.error(f"  - Failed: {path} - {e}")
            
    return {
        "success": True,
        "data": {
            "uploaded": success_count,
            "total": len(files),
            "results": results
        }
    }

async def run_local_test():
    """Run the full agent flow locally with mocked upload"""
    
    print("=" * 60)
    print("LOCAL GENERATION TEST")
    print("=" * 60)
    
    # Clean previous output
    if os.path.exists(TEST_OUTPUT_DIR):
        shutil.rmtree(TEST_OUTPUT_DIR)
    os.makedirs(TEST_OUTPUT_DIR)
    
    # 1. Setup mocks BEFORE importing agent logic to patch the backend calls
    
    # We need to mock 'aiohttp.ClientSession' in 'agent.template_nodes' AND 'agent.execution_layer'
    # Use a simpler approach: Mock the network calls by side-effecting the file write
    
    # Import agent modules
    from agent.graph_logic import create_antigravity_graph
    from agent.state_engine import get_initial_state
    
    # Define User Query
    user_query = "Create a Next.js login page with username and password fields."
    
    print(f"\n[Input] Query: {user_query}")
    print(f"[Output] Directory: {os.path.abspath(TEST_OUTPUT_DIR)}")
    
    # Create graph (No persistence/checkpointer for this test => Auto-approve mode)
    # The graph logic uses `skip_approval=True` if checkpointer is None
    graph = create_antigravity_graph(checkpointer=None, skip_approval=True)
    
    # Initialize State
    initial_state = get_initial_state(
        manifest={"name": "test-app"},
        user_prompt=user_query,
        org_slug=ORG_SLUG,
        project_slug=PROJECT_SLUG,
    )
    
    # 2. Run the graph with mocks
    # specific mocking of `aiohttp.ClientSession.post` context manager
    
    with patch("aiohttp.ClientSession.post") as mock_post:
        # Mock Response object
        mock_response = MagicMock()
        mock_response.status = 200
        
        # Define side effect for json() to return success based on input
        # We also want to capture the payload to write files to disk
        async def mock_json():
            # In a real mock, we'd inspect the call args to get data, 
            # but here we can't easily access the called args from inside the response return
            # So we will rely on the `side_effect` of the mock_post call itself if possible or just log generic success
            return {"success": True, "data": {"uploaded": 1, "total": 1, "results": [{"path": "mock", "success": True}]}}

        mock_response.json = mock_json
        mock_response.__aenter__.return_value = mock_response
        mock_post.return_value = mock_response
        
        # CUSTOM PATCH: We need to intercept the actual file content to write it to disk.
        # The easiest way is to patch `agent.template_nodes.template_upload_node` and `agent.execution_layer.persistence_node`
        # But that's hard to modify widely.
        
        # Better approach: We will check the `state['file_system']` and `state['template_files']` 
        # AT THE END of the run to populate our local folder, 
        # and just letting the "upload" nodes succeed (mocked) without doing anything.
        
        print("\n[Action] Running Agent Graph...")
        
        config = {"configurable": {"thread_id": "local-test-1"}}
        result = await graph.ainvoke(initial_state, config=config)
        
        print("\n[Result] Agent execution completed.")
        
        # 3. Dump files to disk
        template_files = result.get("template_files", {})
        generated_files = result.get("file_system", {})
        
        print(f"\n[Template Files] {len(template_files)} files")
        print(f"[Generated Files] {len(generated_files)} files")
        
        # Merge and write
        all_files = {**template_files, **generated_files}
        
        for path, content in all_files.items():
            full_path = os.path.join(TEST_OUTPUT_DIR, path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
                
        print(f"\n✅ Successfully wrote {len(all_files)} files to '{TEST_OUTPUT_DIR}'")
        
        # 4. Verify specific files exist
        required_files = [
            "package.json",
            "app/layout.tsx",
            "app/page.tsx", # Main page (or login page if agent replaced it)
        ]
        
        # Also check for likely generated files based on prompt
        expected_generated = [
            "app/login/page.tsx",
            "components/login-form.tsx" # or similar
        ]
        
        print("\n[Verification]")
        for f in required_files:
            if os.path.exists(os.path.join(TEST_OUTPUT_DIR, f)):
                 print(f"  ✓ Found template/core file: {f}")
            else:
                 print(f"  ✗ Missing core file: {f}")
                 
        # Fuzzy check for generated content
        found_login = False
        for root, _, files in os.walk(TEST_OUTPUT_DIR):
            for file in files:
                if "login" in file.lower():
                    print(f"  ✓ Found generated file: {os.path.relpath(os.path.join(root, file), TEST_OUTPUT_DIR)}")
                    found_login = True
        
        if not found_login:
            print("  Warning: No explicit 'login' file found. Dictionary check:")
            # Check file keys directly in case they weren't written correctly?
            # No, if write succeeded they are there.
            pass

if __name__ == "__main__":
    asyncio.run(run_local_test())
