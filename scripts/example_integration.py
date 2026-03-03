"""
Integration Example: Using Antigravity Agent from External System

This example shows how to integrate the Antigravity agent into your
existing backend system.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()


class AntigravityClient:
    """
    Client for interacting with the Antigravity agent.
    
    Use this class to integrate the agent into your backend.
    """
    
    def __init__(self):
        """Initialize the client."""
        self._graph = None
        self._checkpointer = None
    
    async def _ensure_graph(self):
        """Lazy initialization of the graph."""
        if self._graph is None:
            from agent.graph_logic import create_antigravity_graph
            # Create without Redis for simplicity (add Redis for production)
            self._graph = create_antigravity_graph(
                checkpointer=None,
                enable_reflexion=False,  # Disable for simple demo
            )
        return self._graph
    
    async def create_project(
        self,
        manifest: dict,
        user_prompt: str,
        session_id: str,
    ) -> dict:
        """
        Create a new frontend project.
        
        Args:
            manifest: Backend API manifest
            user_prompt: User's frontend requirements
            session_id: Unique session identifier
        
        Returns:
            Result with implementation_plan
        """
        from agent.state_engine import AgentState
        
        graph = await self._ensure_graph()
        
        # Initial state
        initial_state = {
            "manifest": manifest,
            "user_prompt": user_prompt,
            "file_system": {},
            "implementation_plan": [],
            "build_logs": [],
            "iteration_count": 0,
            "messages": [],
            "approved": False,
            "build_ready": False,
            "build_status": "pending",
        }
        
        config = {"configurable": {"thread_id": session_id}}
        
        # Run the graph (will pause at approval)
        result = await graph.ainvoke(initial_state, config=config)
        
        return {
            "session_id": session_id,
            "plan": result.get("implementation_plan", []),
            "status": "awaiting_approval",
        }
    
    async def approve_plan(self, session_id: str) -> dict:
        """
        Approve the plan and generate code.
        
        For production, you'd use resume_antigravity_agent with Redis.
        This simplified version re-runs with approved=True.
        """
        # In production, use:
        # from agent import resume_antigravity_agent
        # return await resume_antigravity_agent(session_id, "APPROVE")
        
        raise NotImplementedError(
            "Approval requires Redis checkpointing. "
            "Use resume_antigravity_agent() with Redis enabled."
        )
    
    async def quick_generate(
        self,
        manifest: dict,
        user_prompt: str,
    ) -> dict:
        """
        Quick generation without HITL (auto-approve).
        
        Useful for automated pipelines.
        """
        from agent.graph_logic import get_planning_llm, ARCHITECT_PROMPT
        from agent.execution_layer import get_generation_llm, BUILDER_PROMPT
        from langchain_core.messages import HumanMessage, SystemMessage
        
        files = {}
        
        # Step 1: Generate plan
        print("Generating plan...")
        planning_llm = get_planning_llm()
        
        plan_response = await planning_llm.ainvoke([
            SystemMessage(content=ARCHITECT_PROMPT),
            HumanMessage(content=f"""
## Manifest
{json.dumps(manifest, indent=2)}

## User Request
{user_prompt}

## Instructions
Generate an implementation plan as a JSON array.
""")
        ])
        
        plan_content = plan_response.content.strip()
        if plan_content.startswith("```"):
            lines = plan_content.split("\n")[1:-1]
            plan_content = "\n".join(lines)
        
        plan = json.loads(plan_content)
        print(f"Plan: {len(plan)} tasks")
        
        # Step 2: Generate code for each task
        gen_llm = get_generation_llm()
        
        for task in plan:
            file_path = task.get("file_path", "")
            description = task.get("description", "")
            
            if not file_path:
                continue
            
            print(f"Generating: {file_path}")
            
            response = await gen_llm.ainvoke([
                SystemMessage(content=BUILDER_PROMPT),
                HumanMessage(content=f"""
## Task
{description}

## File Path
{file_path}

## Manifest
{json.dumps(manifest, indent=2)}

## Instructions
Generate complete file content. Return ONLY the code.
""")
            ])
            
            code = response.content.strip()
            if code.startswith("```"):
                lines = code.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                code = "\n".join(lines)
            
            files[file_path] = code
        
        return {
            "plan": plan,
            "files": files,
            "file_count": len(files),
        }


# Example usage
async def main():
    """Demonstrate the integration client."""
    
    client = AntigravityClient()
    
    # Example manifest (from your backend)
    manifest = {
        "name": "my-saas-app",
        "endpoints": [
            {"path": "/api/users", "method": "GET", "response": {"type": "User[]"}},
            {"path": "/api/users/{id}", "method": "GET", "response": {"type": "User"}},
            {"path": "/api/projects", "method": "POST", "body": {"type": "CreateProject"}, "response": {"type": "Project"}},
        ],
        "auth": {"type": "jwt", "provider": "clerk"},
        "database": {"type": "postgresql", "orm": "prisma"},
    }
    
    user_prompt = """
    Create a modern SaaS dashboard with:
    - User management section
    - Project list with create button
    - Dark mode support
    - Responsive design
    """
    
    print("=" * 60)
    print("INTEGRATION EXAMPLE: Quick Generate")
    print("=" * 60)
    
    result = await client.quick_generate(manifest, user_prompt)
    
    print(f"\n✓ Generated {result['file_count']} files:")
    for path in result['files']:
        content = result['files'][path]
        print(f"  - {path} ({len(content)} bytes)")
    
    # Show one file
    first_file = next(iter(result['files'].keys()))
    print(f"\n📄 Sample: {first_file}")
    print("-" * 40)
    for line in result['files'][first_file].split("\n")[:20]:
        print(line)
    print("-" * 40)
    
    # Save files to disk (optional)
    output_dir = "generated_project"
    os.makedirs(output_dir, exist_ok=True)
    
    for path, content in result['files'].items():
        full_path = os.path.join(output_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
    
    print(f"\n✓ Files saved to: {output_dir}/")
    
    return result


if __name__ == "__main__":
    asyncio.run(main())
