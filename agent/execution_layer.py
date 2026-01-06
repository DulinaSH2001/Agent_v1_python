"""
Antigravity Agent - Execution Layer

This module provides the execution layer for the Antigravity agent:
- MCPWrapper: Tool layer using langchain-mcp-adapters for documentation access
- GenerationNode: The Builder - generates code using LLM with MCP tools
- PersistenceNode: The Uploader - uploads virtual file system to Azure Blob Storage

The execution layer implements the code generation and persistence phase
of the Antigravity workflow, executing after human approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agent.state_engine import AgentState

# Load environment variables
load_dotenv()

# Import code validation
from agent.code_validator import validate_file

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# System Prompts for Code Generation
# =============================================================================

BUILDER_PROMPT = """You are the Builder, a senior frontend engineer generating production-ready Next.js 16 code.

## Your Role
Generate TypeScript/TSX code for Next.js 16 applications based on:
1. The implementation task description
2. Backend API manifest for data types
3. Existing file content (for modifications)

## STRICT Next.js 16 Compliance Rules

### 1. Schema Validation
Always use Zod for schema validation:
```typescript
import { z } from 'zod';

const UserSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  name: z.string().min(1),
});

type User = z.infer<typeof UserSchema>;
```

### 2. Server Components
Use async functions for Server Components:
```typescript
export default async function DashboardPage() {
  const data = await fetchData();
  return <div>{/* content */}</div>;
}
```

### 3. Data Caching
Use 'use cache' directive for expensive operations:
```typescript
async function getExpensiveData() {
  'use cache';
  // Expensive fetch or computation
  return await db.query(...);
}
```

### 4. Server Actions
Define in lib/actions.ts with 'use server':
```typescript
'use server';

import { z } from 'zod';

const CreateUserSchema = z.object({
  email: z.string().email(),
  name: z.string(),
});

export async function createUser(formData: FormData) {
  const validated = CreateUserSchema.parse({
    email: formData.get('email'),
    name: formData.get('name'),
  });
  // Action logic
}
```

### 5. Shadcn UI Components
Import from @/components/ui:
```typescript
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
```

### 6. TypeScript Strict Mode
- Never use `any` type
- Define explicit interfaces for props
- Use proper generic types

### 7. File Structure
- Pages: `app/[route]/page.tsx`
- Layouts: `app/[route]/layout.tsx`
- Components: `components/[name].tsx`
- UI Components: `components/ui/[name].tsx`
- Actions: `lib/actions.ts` or `lib/actions/[domain].ts`
- Types: `types/[domain].ts`
- Utilities: `lib/utils.ts`

## Output Format
Return ONLY the TypeScript/TSX code. No markdown formatting, no explanations.
Start directly with imports or 'use server'/'use client' directive if needed.
"""

DELTA_GENERATION_INSTRUCTION = """
## CRITICAL: MODIFICATION MODE

You are modifying an existing file. Follow these rules:

1. **Preserve Existing Code**: Keep all existing imports, types, and logic that are not being changed
2. **Merge Carefully**: Add new functionality without breaking existing features
3. **Maintain Style**: Match the existing code style and patterns
4. **Update Imports**: Add new imports at the top with existing ones
5. **Return Complete File**: Output the entire modified file, not just changes

The current file content is provided below. Modify it according to the task description.
"""


# =============================================================================
# MCP Tool Definitions (Mock implementations for when MCP server unavailable)
# =============================================================================

class SearchNextjsDocsInput(BaseModel):
    """Input schema for search_nextjs_docs tool."""
    query: str = Field(description="Search query for Next.js documentation")
    topic: Optional[str] = Field(
        default=None,
        description="Specific topic: 'routing', 'data-fetching', 'server-actions', 'caching'"
    )


class GetShadcnComponentInput(BaseModel):
    """Input schema for get_shadcn_component tool."""
    component_name: str = Field(description="Name of the Shadcn component (e.g., 'Button', 'Card')")
    include_variants: bool = Field(
        default=True,
        description="Whether to include component variants"
    )


class MockNextjsDocsTool(BaseTool):
    """Mock tool for searching Next.js documentation."""
    
    name: str = "search_nextjs_docs"
    description: str = "Search Next.js 16 documentation for patterns, APIs, and best practices"
    args_schema: type[BaseModel] = SearchNextjsDocsInput
    
    def _run(self, query: str, topic: Optional[str] = None) -> str:
        """Synchronous run - returns documentation snippets."""
        return self._get_docs(query, topic)
    
    async def _arun(self, query: str, topic: Optional[str] = None) -> str:
        """Async run - returns documentation snippets."""
        return self._get_docs(query, topic)
    
    def _get_docs(self, query: str, topic: Optional[str]) -> str:
        """Get mock documentation based on query."""
        docs = {
            "server-actions": """
# Server Actions in Next.js 16

Server Actions are async functions that execute on the server. They can be used in forms.

```typescript
// lib/actions.ts
'use server';

export async function createItem(formData: FormData) {
  const name = formData.get('name') as string;
  // Insert into database
  return { success: true };
}
```

Usage in components:
```typescript
import { createItem } from '@/lib/actions';

export default function Form() {
  return (
    <form action={createItem}>
      <input name="name" />
      <button type="submit">Create</button>
    </form>
  );
}
```
""",
            "routing": """
# App Router in Next.js 16

Use the app/ directory for routing:
- `app/page.tsx` - Home page (/)
- `app/dashboard/page.tsx` - Dashboard (/dashboard)
- `app/[id]/page.tsx` - Dynamic route (/:id)
- `app/(group)/page.tsx` - Route groups
""",
            "data-fetching": """
# Data Fetching in Next.js 16

Server Components can fetch data directly:

```typescript
export default async function Page() {
  const data = await fetch('https://api.example.com/data');
  return <div>{data}</div>;
}
```

Use 'use cache' for expensive operations:
```typescript
async function getData() {
  'use cache';
  return await expensiveOperation();
}
```
""",
            "caching": """
# Caching in Next.js 16

Use the 'use cache' directive:

```typescript
async function getCachedData() {
  'use cache';
  const res = await fetch('https://api.example.com/data');
  return res.json();
}
```

Options: revalidate, tags
""",
        }
        
        if topic and topic in docs:
            return docs[topic]
        
        # Search across all docs
        for key, doc in docs.items():
            if query.lower() in key or query.lower() in doc.lower():
                return doc
        
        return f"No specific documentation found for '{query}'. Use standard Next.js 16 patterns."


class MockShadcnComponentTool(BaseTool):
    """Mock tool for getting Shadcn UI component patterns."""
    
    name: str = "get_shadcn_component"
    description: str = "Get Shadcn UI component usage patterns and variants"
    args_schema: type[BaseModel] = GetShadcnComponentInput
    
    def _run(self, component_name: str, include_variants: bool = True) -> str:
        """Synchronous run."""
        return self._get_component(component_name, include_variants)
    
    async def _arun(self, component_name: str, include_variants: bool = True) -> str:
        """Async run."""
        return self._get_component(component_name, include_variants)
    
    def _get_component(self, component_name: str, include_variants: bool) -> str:
        """Get component pattern."""
        components = {
            "button": """
# Button Component

```typescript
import { Button } from '@/components/ui/button';

// Variants: default, destructive, outline, secondary, ghost, link
// Sizes: default, sm, lg, icon

<Button variant="default" size="default">Click me</Button>
<Button variant="destructive">Delete</Button>
<Button variant="outline" size="sm">Small</Button>
<Button asChild><a href="/link">Link</a></Button>
```
""",
            "card": """
# Card Component

```typescript
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

<Card>
  <CardHeader>
    <CardTitle>Title</CardTitle>
    <CardDescription>Description</CardDescription>
  </CardHeader>
  <CardContent>
    <p>Content here</p>
  </CardContent>
  <CardFooter>
    <Button>Action</Button>
  </CardFooter>
</Card>
```
""",
            "input": """
# Input Component

```typescript
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

<div className="space-y-2">
  <Label htmlFor="email">Email</Label>
  <Input id="email" type="email" placeholder="Enter email" />
</div>
```
""",
            "form": """
# Form with React Hook Form + Zod

```typescript
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

const formSchema = z.object({
  email: z.string().email(),
});

export function MyForm() {
  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
  });

  function onSubmit(values: z.infer<typeof formSchema>) {
    console.log(values);
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit">Submit</Button>
      </form>
    </Form>
  );
}
```
""",
            "table": """
# Table Component

```typescript
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

<Table>
  <TableHeader>
    <TableRow>
      <TableHead>Name</TableHead>
      <TableHead>Email</TableHead>
    </TableRow>
  </TableHeader>
  <TableBody>
    {items.map((item) => (
      <TableRow key={item.id}>
        <TableCell>{item.name}</TableCell>
        <TableCell>{item.email}</TableCell>
      </TableRow>
    ))}
  </TableBody>
</Table>
```
""",
            "dialog": """
# Dialog Component

```typescript
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';

<Dialog>
  <DialogTrigger asChild>
    <Button>Open Dialog</Button>
  </DialogTrigger>
  <DialogContent>
    <DialogHeader>
      <DialogTitle>Title</DialogTitle>
      <DialogDescription>Description</DialogDescription>
    </DialogHeader>
    <div>Content</div>
    <DialogFooter>
      <Button>Save</Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
```
""",
        }
        
        key = component_name.lower()
        if key in components:
            return components[key]
        
        return f"Component '{component_name}' not found. Available: {', '.join(components.keys())}"


# =============================================================================
# MCP Wrapper
# =============================================================================

class MCPWrapper:
    """
    Wrapper for MCP (Model Context Protocol) tool access.
    
    This class provides access to documentation and component tools,
    either via a real MCP server or mock implementations for testing.
    
    Attributes:
        tools: List of available tools (mock or real MCP)
        mcp_available: Whether a real MCP server is connected
    """
    
    def __init__(self):
        """Initialize the MCP wrapper state."""
        self.mcp_available = False
        self.tools: List[BaseTool] = []
        self._initialized = False
        self.client = None
    
    async def initialize(self) -> None:
        """
        Asynchronously set up MCP tools.
        Calls _setup_mock_tools as failure fallback.
        """
        if self._initialized:
            return
            
        mcp_servers_config = os.getenv("MCP_SERVERS_CONFIG")
        mcp_url = os.getenv("MCP_DOCS_SERVER_URL")
        mcp_command = os.getenv("MCP_DOCS_SERVER_COMMAND")
        
        if mcp_servers_config or mcp_url or mcp_command:
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
                
                servers = {}
                
                # Priority 1: Multi-server JSON config
                if mcp_servers_config:
                    try:
                        config_data = json.loads(mcp_servers_config)
                        for name, config in config_data.items():
                            logger.info(f"Configuring MCP server '{name}'")
                            
                            # Handle command args if simple string
                            if "command" in config and "args" not in config:
                                parts = config["command"].split()
                                config["command"] = parts[0]
                                config["args"] = parts[1:]
                            
                            # Ensure transport is set (default to stdio if command is present)
                            if "transport" not in config:
                                if "command" in config:
                                    config["transport"] = "stdio"
                                elif "url" in config:
                                    config["transport"] = "sse"
                            
                            servers[name] = config
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse MCP_SERVERS_CONFIG: {e}")
                
                # Priority 2: Legacy single server config (only if not in servers)
                if not servers:
                    if mcp_url:
                        logger.info(f"Connecting to MCP server via SSE at {mcp_url}")
                        servers["docs_server"] = {
                            "url": mcp_url,
                            "transport": "sse",
                        }
                    elif mcp_command:
                        logger.info(f"Connecting to local MCP server via Stdio: {mcp_command}")
                        # Split command if it's a string
                        cmd_parts = mcp_command.split()
                        servers["docs_server"] = {
                            "command": cmd_parts[0],
                            "args": cmd_parts[1:],
                            "transport": "stdio",
                        }
                
                if servers:
                    self.client = MultiServerMCPClient(servers)
                    # MultiServerMCPClient.get_tools is an async method
                    self.tools = await self.client.get_tools()
                    self.mcp_available = True
                    logger.info(f"Successfully connected to {len(servers)} MCP servers. Found {len(self.tools)} tools.")
                else:
                    logger.warning("No valid MCP servers configured. Falling back to mocks.")
                    self._setup_mock_tools()
                
            except Exception as e:
                logger.warning(f"Failed to connect to real MCP server: {e}. Falling back to mocks.")
                self._setup_mock_tools()
        else:
            logger.info("No MCP server configuration found. Using mock tools.")
            self._setup_mock_tools()
            
        self._initialized = True
    
    def _setup_mock_tools(self) -> None:
        """Set up mock tools for offline operation."""
        self.tools = [
            MockNextjsDocsTool(),
            MockShadcnComponentTool(),
        ]
        self.mcp_available = False
    
    def get_tools(self) -> List[BaseTool]:
        """Get list of available tools. Returns mock tools if not initialized."""
        if not self.tools and not self._initialized:
            self._setup_mock_tools()
        return self.tools
    
    def should_use_tools(self, task: Dict[str, Any]) -> bool:
        """
        Determine if a task should use documentation tools.
        
        Args:
            task: Implementation task with description.
        
        Returns:
            True if task involves Shadcn or Server Actions.
        """
        description = task.get("description", "").lower()
        file_path = task.get("file_path", "").lower()
        
        tool_keywords = [
            "shadcn",
            "server action",
            "use server",
            "form",
            "button",
            "card",
            "dialog",
            "table",
            "component",
            "ui/",
        ]
        
        return any(kw in description or kw in file_path for kw in tool_keywords)


# Global MCP wrapper instance
_mcp_wrapper: Optional[MCPWrapper] = None


async def get_mcp_wrapper() -> MCPWrapper:
    """Get or create the global MCP wrapper instance and initialize it."""
    global _mcp_wrapper
    if _mcp_wrapper is None:
        _mcp_wrapper = MCPWrapper()
    
    # Ensure it's initialized (async)
    if not _mcp_wrapper._initialized:
        await _mcp_wrapper.initialize()
        
    return _mcp_wrapper


# =============================================================================
# LLM Configuration for Generation
# =============================================================================

def get_generation_llm(
    temperature: float = 0.2,
    streaming: bool = True,
) -> ChatOpenAI:
    """
    Get a configured LLM instance for code generation.
    
    Supports both Azure OpenAI and standard OpenAI based on environment variables.
    Checks for Azure config first, then falls back to standard OpenAI.
    
    Args:
        temperature: Sampling temperature. Slightly higher for creative code.
        streaming: Whether to enable streaming.
    
    Returns:
        Configured ChatOpenAI or AzureChatOpenAI instance.
    """
    # Check for Azure OpenAI configuration
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    azure_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    
    if azure_endpoint and azure_key:
        try:
            from langchain_openai import AzureChatOpenAI
            
            logger.info(f"Using Azure OpenAI for generation: {azure_deployment}")
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=azure_key,
                azure_deployment=azure_deployment,
                api_version=azure_version,
                temperature=temperature,
                streaming=streaming,
            )
        except ImportError:
            logger.warning("AzureChatOpenAI not available, falling back to OpenAI")
    
    # Fall back to standard OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "Neither Azure OpenAI nor OpenAI API key is configured. "
            "Set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY or OPENAI_API_KEY."
        )
    
    logger.info("Using standard OpenAI API for generation")
    return ChatOpenAI(
        model="gpt-4o",
        temperature=temperature,
        streaming=streaming,
        api_key=api_key,
    )


# =============================================================================
# Node: GenerationNode (The Builder)
# =============================================================================

async def generation_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    The Builder node - generates code for each task in the implementation plan.
    
    This node iterates through the approved implementation plan and generates
    TypeScript/TSX code for each file. For delta/modify tasks, it includes
    the existing file content as context (Antigravity pattern).
    
    Args:
        state: Current agent state with implementation_plan and file_system.
        config: Runnable configuration.
    
    Returns:
        State update with populated file_system and build_logs.
    """
    logger.info("generation_node: Starting code generation")
    
    # Get mutable copies
    file_system = dict(state.get("file_system", {}))
    build_logs: List[str] = list(state.get("build_logs", []))
    
    # Get the implementation plan
    plan = state.get("implementation_plan", [])
    if not plan:
        logger.warning("generation_node: Empty implementation plan")
        build_logs.append("Warning: No tasks in implementation plan")
        return {"file_system": file_system, "build_logs": build_logs}
    
    # Get LLM and tools
    llm = get_generation_llm()
    mcp = await get_mcp_wrapper()
    tools = mcp.get_tools()
    
    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(tools)
    
    # Get manifest for context
    manifest = state.get("manifest", {})
    manifest_str = json.dumps(manifest, indent=2) if manifest else "No manifest provided"
    
    # Get template files to exclude from generation
    template_files = state.get("template_files", {})
    template_paths = set(template_files.keys())
    
    # Process each task
    for i, task in enumerate(plan):
        task_id = task.get("id", f"task-{i}")
        task_type = task.get("type", "create")
        file_path = task.get("file_path", "")
        description = task.get("description", "")
        
        if not file_path:
            logger.warning(f"generation_node: Task {task_id} has no file_path, skipping")
            build_logs.append(f"Skipped task {task_id}: no file path")
            continue
        
        # Skip template files - they were already uploaded in Phase 1
        if file_path in template_paths and task_type == "create":
            logger.info(f"generation_node: Skipping template file {file_path} (already uploaded)")
            build_logs.append(f"Skipped: {file_path} (from template)")
            continue
        
        if task_type == "delete":
            # Handle file deletion
            if file_path in file_system:
                del file_system[file_path]
                build_logs.append(f"Deleted: {file_path}")
                logger.info(f"generation_node: Deleted {file_path}")
            continue
        
        logger.info(f"generation_node: Processing {task_id} - {task_type} {file_path}")
        
        # Build the prompt
        system_prompt = BUILDER_PROMPT
        
        # Check if this is a modification (Antigravity delta mode)
        existing_content = ""
        if task_type == "modify" and file_path in file_system:
            existing_content = file_system[file_path]
            system_prompt += DELTA_GENERATION_INSTRUCTION
        
        # Build user message
        user_content = f"""## Task
{description}

## File Path
{file_path}

## Backend API Manifest
```json
{manifest_str}
```
"""
        
        if existing_content:
            user_content += f"""
## Current File Content (MODIFY this file)
```typescript
{existing_content}
```
"""
        
        user_content += """
## Instructions
Generate the complete file content. Return ONLY the code, no markdown formatting.
"""
        
        # Check if we should use tools for this task
        use_tools = mcp.should_use_tools(task)
        
        try:
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_content),
            ]
            
            if use_tools:
                # First, query relevant documentation
                logger.info(f"generation_node: Using tools for {task_id}")
                
                # Get relevant docs based on task
                if "shadcn" in description.lower() or "component" in description.lower():
                    # Try to extract component name
                    for comp in ["button", "card", "input", "form", "table", "dialog"]:
                        if comp in description.lower():
                            comp_tool = next((t for t in tools if t.name == "get_shadcn_component"), None)
                            if comp_tool:
                                docs = await comp_tool._arun(comp)
                                messages.append(HumanMessage(content=f"## Shadcn Component Reference\n{docs}"))
                            break
                
                if "server action" in description.lower() or "action" in description.lower():
                    docs_tool = next((t for t in tools if t.name == "search_nextjs_docs"), None)
                    if docs_tool:
                        docs = await docs_tool._arun("server actions", "server-actions")
                        messages.append(HumanMessage(content=f"## Next.js Documentation\n{docs}"))
            
            # Generate the code
            response = await llm.ainvoke(messages, config=config)
            
            # Extract code from response
            code = response.content.strip()
            
            # Clean up potential markdown formatting
            if code.startswith("```"):
                lines = code.split("\n")
                # Remove first line (```typescript or similar)
                lines = lines[1:]
                # Remove last line if it's just ```
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                code = "\n".join(lines)
            
            # Store in file system
            file_system[file_path] = code
            build_logs.append(f"Generated: {file_path} ({len(code)} bytes)")
            logger.info(f"generation_node: Generated {file_path}")
            
        except Exception as e:
            error_msg = f"Failed to generate {file_path}: {str(e)}"
            logger.error(f"generation_node: {error_msg}")
            build_logs.append(f"Error: {error_msg}")
    
    logger.info(f"generation_node: Completed. Generated {len(file_system)} files.")
    
    return {
        "file_system": file_system,
        "build_logs": build_logs,
    }


# =============================================================================
# Node: PersistenceNode (The Uploader)
# =============================================================================


# =============================================================================
# Azure SAS Token Helper
# =============================================================================

async def request_sas_token(container_name: str) -> Optional[Dict[str, Any]]:
    """
    Request a SAS token from the backend for Azure Blob Storage uploads.
    
    This allows the Agent to upload files without storing Azure credentials locally,
    and avoids SSL certificate verification issues.
    
    Args:
        container_name: Name of the Azure container to upload to
        
    Returns:
        Dict with sasUrl, containerUrl, expiresOn, etc., or None if failed
    """
    backend_url = os.getenv("BACKEND_URL", "http://localhost:4000")
    webhook_secret = os.getenv("FASTAPI_WEBHOOK_SECRET")
    
    if not webhook_secret:
        logger.warning("FASTAPI_WEBHOOK_SECRET not set. Cannot authenticate SAS token request.")
        return None
    
    try:
        import aiohttp
        
        endpoint = f"{backend_url}/api/v1/azure/sas-token"
        payload = {
            "containerName": container_name,
            "expiresInMinutes": 60,
            "permissions": "racwdl"  # read, add, create, write, delete, list
        }
        
        headers = {
            "Authorization": f"Bearer {webhook_secret}",
            "Content-Type": "application/json"
        }
        
        logger.info(f"Requesting SAS token from {endpoint}")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("success"):
                        logger.info("Successfully obtained SAS token")
                        return data.get("data")
                    else:
                        logger.error(f"SAS token request failed: {data.get('error')}")
                        return None
                else:
                    text = await response.text()
                    logger.error(f"SAS token request failed with status {response.status}: {text}")
                    return None
                    
    except ImportError:
        logger.error("aiohttp not installed. Run: pip install aiohttp")
        return None
    except Exception as e:
        logger.error(f"Failed to request SAS token: {e}")
        return None


# =============================================================================
# Node: PersistenceNode (The Uploader)
# =============================================================================

async def persistence_node(

    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    The Uploader node - uploads the virtual file system to Azure Blob Storage.
    
    This node takes all files from state['file_system'] and uploads them
    to an Azure Blob Storage container named 'project-{thread_id}'.
    Directory structure is preserved in blob names.
    
    Args:
        state: Current agent state with file_system.
        config: Runnable configuration with thread_id.
    
    Returns:
        State update with build_ready flag and build_logs.
    """
    logger.info("persistence_node: Starting file upload to Azure Blob Storage")
    
    # Get mutable build logs
    build_logs: List[str] = list(state.get("build_logs", []))
    
    # Get the file system
    file_system = state.get("file_system", {})
    if not file_system:
        logger.warning("persistence_node: No files to upload")
        build_logs.append("Warning: No files in file system to upload")
        return {"build_ready": False, "build_logs": build_logs}
    
    # Get thread_id for container naming
    thread_id = config.get("configurable", {}).get("thread_id", "unknown")
    container_name = f"project-{thread_id}"
    
    # Sanitize container name (Azure requirements: lowercase, alphanumeric and hyphens)
    container_name = "".join(c if c.isalnum() or c == "-" else "-" for c in container_name.lower())
    
    # Try SAS token authentication first (preferred method)
    sas_data = await request_sas_token(container_name)
    
    if not sas_data:
        # Fallback to connection string if SAS token fails
        logger.warning("persistence_node: SAS token unavailable, trying connection string fallback")
        connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        
        if not connection_string:
            logger.warning("persistence_node: No Azure credentials available. Skipping upload.")
            build_logs.append("Warning: Azure credentials not configured. Files stored in memory only.")
            build_logs.append(f"Files ready for upload: {len(file_system)}")
            for path in file_system.keys():
                build_logs.append(f"  - {path}")
            return {"build_ready": False, "build_logs": build_logs}
    
    try:
        # Import Azure SDK
        from azure.storage.blob.aio import BlobServiceClient, ContainerClient
        from azure.core.exceptions import ResourceExistsError
        
        # Create BlobServiceClient using SAS token or connection string
        if sas_data:
            logger.info(f"Using SAS token authentication for container {container_name}")
            sas_url = sas_data.get("sasUrl")
            blob_service = BlobServiceClient(account_url=sas_url)
            container_client = blob_service.get_container_client(container_name)
            build_logs.append(f"Connected to Azure using SAS token (expires: {sas_data.get('expiresOn')})")
        else:
            logger.info(f"Using connection string authentication for container {container_name}")
            blob_service = BlobServiceClient.from_connection_string(connection_string)
            container_client = blob_service.get_container_client(container_name)
        
        async with blob_service:
            # Ensure container exists
            try:
                await container_client.create_container()
                build_logs.append(f"Created container: {container_name}")
                logger.info(f"persistence_node: Created container {container_name}")
            except ResourceExistsError:
                build_logs.append(f"Using existing container: {container_name}")
                logger.info(f"persistence_node: Container {container_name} exists")
            except Exception as e:
                # Container may already exist or we don't have permission to create
                logger.info(f"persistence_node: Container check: {e}")
            
            # Upload each file
            upload_count = 0
            for file_path, content in file_system.items():
                try:
                    # Use file path as blob name (preserves directory structure)
                    blob_client = container_client.get_blob_client(file_path)
                    
                    # Upload content as bytes
                    await blob_client.upload_blob(
                        content.encode("utf-8"),
                        overwrite=True,
                    )
                    
                    upload_count += 1
                    logger.debug(f"persistence_node: Uploaded {file_path}")
                    
                except Exception as e:
                    error_msg = f"Failed to upload {file_path}: {str(e)}"
                    logger.error(f"persistence_node: {error_msg}")
                    build_logs.append(f"Error: {error_msg}")
            
            build_logs.append(f"Uploaded {upload_count}/{len(file_system)} files to Azure")
            logger.info(f"persistence_node: Uploaded {upload_count} files to {container_name}")
        
        return {"build_ready": True, "build_logs": build_logs}

        
    except ImportError:
        logger.error("persistence_node: azure-storage-blob package not installed")
        build_logs.append("Error: Azure SDK not installed. Run: pip install azure-storage-blob")
        return {"build_ready": False, "build_logs": build_logs}
        
    except Exception as e:
        error_msg = f"Azure upload failed: {str(e)}"
        logger.error(f"persistence_node: {error_msg}")
        build_logs.append(f"Error: {error_msg}")
        return {"build_ready": False, "build_logs": build_logs}


# =============================================================================
# Utility Functions
# =============================================================================

def list_generated_files(file_system: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Get a summary of generated files.
    
    Args:
        file_system: Virtual file system dictionary.
    
    Returns:
        List of file summaries with path, size, and type.
    """
    files = []
    for path, content in file_system.items():
        file_type = "unknown"
        if path.endswith(".tsx"):
            file_type = "react-component"
        elif path.endswith(".ts"):
            if "actions" in path:
                file_type = "server-action"
            elif "types" in path:
                file_type = "type-definition"
            else:
                file_type = "typescript"
        elif path.endswith(".css"):
            file_type = "stylesheet"
        elif path.endswith(".json"):
            file_type = "config"
        
        files.append({
            "path": path,
            "size": len(content),
            "lines": content.count("\n") + 1,
            "type": file_type,
        })
    
    return sorted(files, key=lambda f: f["path"])


async def preview_generation(
    task: Dict[str, Any],
    existing_content: Optional[str] = None,
) -> str:
    """
    Preview what would be generated for a single task.
    
    Useful for testing the generation prompt without running the full pipeline.
    
    Args:
        task: Single implementation task.
        existing_content: Optional existing file content for delta mode.
    
    Returns:
        Generated code preview.
    """
    llm = get_generation_llm()
    
    system_prompt = BUILDER_PROMPT
    if existing_content:
        system_prompt += DELTA_GENERATION_INSTRUCTION
    
    user_content = f"""## Task
{task.get("description", "No description")}

## File Path
{task.get("file_path", "unknown.tsx")}
"""
    
    if existing_content:
        user_content += f"""
## Current File Content
```typescript
{existing_content}
```
"""
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    
    response = await llm.ainvoke(messages)
    return response.content
