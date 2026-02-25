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

# Import code validation and analysis
from agent.code_validator import validate_file
from agent.codebase_analyzer import CodebaseAnalyzer

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# System Prompts for Code Generation
# =============================================================================

BUILDER_PROMPT = """You are the Builder, a senior frontend engineer generating production-ready Next.js 15 code.

## Your Role
Generate TypeScript/TSX code for Next.js 15 applications based on:
1. The implementation task description
2. Backend API manifest for data types
3. Existing file content (for modifications)

## STRICT Next.js 15 Compliance Rules

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

### 3. Data Fetching & Caching
Use fetch with cache options in Server Components:
```typescript
// Static (cached forever)
const data = await fetch('https://api.example.com/data', { cache: 'force-cache' });
// Revalidate every 60 seconds (ISR)
const data = await fetch('https://api.example.com/data', { next: { revalidate: 60 } });
// Dynamic (no cache)
const data = await fetch('https://api.example.com/data', { cache: 'no-store' });
```
Do NOT use `'use cache'` — it is an experimental directive that will cause build failures.

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
ONLY import from these EXACT files that exist in @/components/ui:
```typescript
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
```

NEVER import from `@/components/ui/header`, `@/components/ui/footer`, `@/components/ui/navbar`,
`@/components/ui/sidebar`, `@/components/ui/dropdown-menu`, `@/components/ui/select`,
`@/components/ui/tabs`, `@/components/ui/toast`, or any other path not listed above.
If you need a header, footer, or nav, create them as custom components in `components/`.

### 6. TypeScript Strict Mode
- Prefer `unknown` over `any`; never use `any` for props or return types
- Define explicit interfaces for props
- Use proper generic types

### 7. File Structure
- Pages: `app/[route]/page.tsx`
- Layouts: `app/[route]/layout.tsx`
- Error boundaries: `app/[route]/error.tsx` (MUST be 'use client')
- Loading UI: `app/[route]/loading.tsx` (server component, no directive needed)
- Components: `components/[name].tsx`
- UI Components: `components/ui/[name].tsx`
- Actions: `lib/actions.ts` or `lib/actions/[domain].ts`
- Types: `types/[domain].ts`
- Utilities: `lib/utils.ts`

### 8. Required patterns for special files

**error.tsx** — MUST use exactly this pattern:
```typescript
'use client';

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';

interface ErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function Error({ error, reset }: ErrorProps) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center min-h-[400px] gap-4">
      <h2 className="text-xl font-semibold">Something went wrong</h2>
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
```

**loading.tsx** — MUST use exactly this pattern (no 'use client'):
```typescript
import { Skeleton } from '@/components/ui/skeleton';

export default function Loading() {
  return (
    <div className="space-y-4 p-6">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/4" />
    </div>
  );
}
```

### 9. Caching
Do NOT use `'use cache'` directive — it is experimental and will break the build.
Use `fetch` with `cache` option instead:
```typescript
const data = await fetch('/api/data', { cache: 'force-cache' }); // static
const data = await fetch('/api/data', { next: { revalidate: 60 } }); // ISR
const data = await fetch('/api/data', { cache: 'no-store' }); // dynamic
```

### 10. PROTECTED FILES — DO NOT GENERATE
These files already exist in the template and are managed outside this generation:
- `app/layout.tsx` — NEVER modify or regenerate
- `app/page.tsx` — NEVER modify or regenerate (or generate custom pages in `app/[route]/page.tsx` instead)
- `styles/globals.css` — NEVER modify
- `tailwind.config.js` — NEVER modify
- `next.config.js` — NEVER modify
- `tsconfig.json` — NEVER modify

If you need a home page, create `app/dashboard/page.tsx` or other route-specific pages.
If you need layout changes beyond the root, create nested `app/[route]/layout.tsx` for specific routes.

### 11. Component Usage Rules — CRITICAL
Before using ANY custom component:
1. **Check the "Available Components" section** provided below the task description
2. **Verify it exists** in the list with exact name
3. **Match required props exactly** — do NOT invent prop names or types
4. **Never hallucinate components** — if a component is not listed, do NOT use it

Common mistake example:
```typescript
// ❌ WRONG - Component not in inventory, or props are wrong
<Header links={...} branding={...} />

// ✅ CORRECT - Use only components from the available list with correct props
// If Header is not listed, create it in components/Header.tsx first
```

If you need a component that is NOT in the "Available Components" list:
- Option A: Create it in `components/[ComponentName].tsx`
- Option B: Use a Shadcn UI component that IS available
- Option C: Use simple HTML elements

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
    description: str = "Search Next.js 15 documentation for patterns, APIs, and best practices"
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
# Server Actions in Next.js 15

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
# App Router in Next.js 15

Use the app/ directory for routing:
- `app/page.tsx` - Home page (/)
- `app/dashboard/page.tsx` - Dashboard (/dashboard)
- `app/[id]/page.tsx` - Dynamic route (/:id)
- `app/(group)/page.tsx` - Route groups
""",
            "data-fetching": """
# Data Fetching in Next.js 15

Server Components can fetch data directly:

```typescript
export default async function Page() {
  const res = await fetch('https://api.example.com/data', { cache: 'no-store' });
  const data = await res.json();
  return <div>{JSON.stringify(data)}</div>;
}
```

Use fetch cache options for performance:
```typescript
// Static: cached indefinitely
const res = await fetch(url, { cache: 'force-cache' });
// ISR: revalidate every 60s
const res = await fetch(url, { next: { revalidate: 60 } });
// Dynamic: always fresh
const res = await fetch(url, { cache: 'no-store' });
```
""",
            "caching": """
# Caching in Next.js 15

Use fetch cache options — do NOT use `'use cache'` directive (experimental, breaks builds):

```typescript
// Static caching
const res = await fetch('https://api.example.com/data', { cache: 'force-cache' });
// Time-based revalidation (ISR)
const res = await fetch('https://api.example.com/data', { next: { revalidate: 3600 } });
// Tag-based revalidation
const res = await fetch('https://api.example.com/data', { next: { tags: ['products'] } });
```
""",
        }
        
        if topic and topic in docs:
            return docs[topic]
        
        # Search across all docs
        for key, doc in docs.items():
            if query.lower() in key or query.lower() in doc.lower():
                return doc
        
        return f"No specific documentation found for '{query}'. Use standard Next.js 15 patterns."


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
# Streaming Helpers - Per-file upload and Ably publish
# =============================================================================

async def publish_file_generated(
    thread_id: str,
    file_path: str,
    content: str,
    task_index: int,
    total_tasks: int,
) -> bool:
    """
    Publish a file_generated event to Ably so the frontend can
    incrementally mount files into WebContainer as they are created.

    Args:
        thread_id: Job/thread identifier for the Ably channel.
        file_path: Path of the generated file.
        content: Generated file content.
        task_index: 0-based index of the current task.
        total_tasks: Total number of tasks in the plan.

    Returns:
        True if published successfully.
    """
    try:
        from ably import AblyRealtime

        api_key = os.getenv("ABLY_API_KEY")
        if not api_key:
            logger.warning("ABLY_API_KEY not set, skipping file_generated publish")
            return False

        client = AblyRealtime(api_key)
        channel_name = f"ai-backend-generation:{thread_id}"
        channel = client.channels.get(channel_name)

        await channel.publish("file_generated", {
            "file_path": file_path,
            "content": content,
            "content_size": len(content),
            "task_index": task_index,
            "total_tasks": total_tasks,
            "progress": int(((task_index + 1) / total_tasks) * 100) if total_tasks else 0,
            "message": f"Generated {file_path} ({task_index + 1}/{total_tasks})",
        })
        await client.close()

        logger.info(f"Published file_generated for {file_path} ({task_index + 1}/{total_tasks})")
        return True

    except ImportError:
        logger.warning("Ably package not installed, skipping file_generated publish")
        return False
    except Exception as e:
        logger.error(f"Failed to publish file_generated: {e}")
        return False


async def stream_file_to_backend(
    file_path: str,
    content: str,
    org_slug: str,
    project_slug: str,
    job_id: str,
) -> bool:
    """
    Upload a single generated file to the backend immediately after generation.

    This enables real-time file streaming instead of batch uploads, so the
    frontend can display and preview files as they are created.

    Args:
        file_path: Relative path of the file (e.g., "app/page.tsx").
        content: File content string.
        org_slug: Organization slug for storage path.
        project_slug: Project slug for storage path.
        job_id: Generation job ID for tracking.

    Returns:
        True if upload succeeded.
    """
    backend_url = os.getenv("BACKEND_URL", "http://localhost:8080")
    webhook_secret = os.getenv("FASTAPI_WEBHOOK_SECRET", "")

    try:
        import aiohttp

        endpoint = f"{backend_url}/api/v1/agents/upload-file"
        payload = {
            "orgSlug": org_slug,
            "projectSlug": project_slug,
            "path": file_path,
            "content": content,
            "jobId": job_id,
        }

        headers = {
            "Authorization": f"Bearer {webhook_secret}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, headers=headers, timeout=30) as response:
                if response.status == 200:
                    logger.info(f"Streamed file to backend: {file_path}")
                    return True
                else:
                    text = await response.text()
                    logger.error(f"Failed to stream {file_path}: status {response.status} - {text}")
                    return False

    except ImportError:
        logger.error("aiohttp not installed, cannot stream file")
        return False
    except Exception as e:
        logger.error(f"Failed to stream file {file_path}: {e}")
        return False


# =============================================================================
# Phase 3: Modification Nodes
# =============================================================================

async def modification_analysis_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    Analyze which files are affected by a modification request.

    Uses CodebaseAnalyzer to find target files and build dependency impact.
    Injects affected files and dependency analysis into the state for planning.
    """
    from agent.codebase_analyzer import CodebaseAnalyzer

    logger.info("=== Modification Analysis Node ===")

    config_data = config.get("configurable", {}) if config else {}
    org_slug = config_data.get("org_slug", "")
    project_slug = config_data.get("project_slug", "")

    try:
        # Initialize analyzer
        analyzer = CodebaseAnalyzer()

        # Analyze existing file system (from state)
        if state.get("file_system"):
            analyzer.analyze_files(state["file_system"])

            # Find affected files based on modification request
            modification_request = state.get("user_message", state.get("query", ""))
            affected_files = analyzer.find_affected_files(modification_request, state["file_system"])

            # Get codebase summary for LLM context
            codebase_summary = analyzer.get_file_summary()

            # Analyze impact of each affected file
            file_impacts = {
                file_path: analyzer.analyze_file_impact(file_path)
                for file_path in affected_files
            }

            logger.info(f"Found {len(affected_files)} affected files")
            logger.info(f"File impacts: {json.dumps(file_impacts, default=str, indent=2)}")

            # Publish progress to Ably
            if state.get("thread_id"):
                publish_to_ably(
                    thread_id=state["thread_id"],
                    event_name="modification_analysis",
                    data={
                        "affected_files": affected_files,
                        "file_count": len(affected_files),
                        "message": f"Analyzing {len(affected_files)} files for modification"
                    }
                )

            return {
                "modification_analysis": {
                    "affected_files": affected_files,
                    "file_impacts": file_impacts,
                    "codebase_summary": codebase_summary,
                    "analyzer_state": analyzer.to_dict(),
                }
            }
        else:
            logger.warning("No file system in state for modification analysis")
            return {
                "modification_analysis": {
                    "affected_files": [],
                    "file_impacts": {},
                    "codebase_summary": "No codebase to analyze",
                    "analyzer_state": {},
                }
            }

    except Exception as e:
        logger.error(f"Modification analysis failed: {e}", exc_info=True)
        return {
            "modification_analysis": {
                "affected_files": [],
                "file_impacts": {},
                "error": str(e),
            }
        }


async def modification_planning_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    Create a targeted modification plan based on analysis.

    Takes the affected files and creates a focused modification plan
    that instructs the generator to only modify specific sections.
    """
    logger.info("=== Modification Planning Node ===")

    config_data = config.get("configurable", {}) if config else {}
    org_slug = config_data.get("org_slug", "")
    project_slug = config_data.get("project_slug", "")

    try:
        analysis = state.get("modification_analysis", {})
        affected_files = analysis.get("affected_files", [])

        if not affected_files:
            logger.warning("No affected files to plan modifications for")
            return {
                "delta_mode": True,
                "modification_targets": [],
            }

        # Build modification instruction for the generator
        modification_targets = []
        for file_path in affected_files[:5]:  # Limit to top 5 to avoid token explosion
            file_impact = analysis.get("file_impacts", {}).get(file_path, {})
            scope = file_impact.get("impact_scope", 0)

            modification_targets.append({
                "file_path": file_path,
                "operation": "modify",  # vs "create" or "delete"
                "scope": scope,
                "dependencies": file_impact.get("imported_by", [])[:3],  # Top 3 dependents
            })

        logger.info(f"Created modification targets for {len(modification_targets)} files")

        # Publish plan to Ably
        if state.get("thread_id"):
            publish_to_ably(
                thread_id=state["thread_id"],
                event_name="modification_plan_ready",
                data={
                    "targets": modification_targets,
                    "message": f"Modification plan ready for {len(modification_targets)} files"
                }
            )

        return {
            "delta_mode": True,
            "modification_targets": modification_targets,
        }

    except Exception as e:
        logger.error(f"Modification planning failed: {e}", exc_info=True)
        return {
            "delta_mode": True,
            "modification_targets": [],
            "error": str(e),
        }


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

    After each file is generated, it is:
    1. Published to Ably as a `file_generated` event (for real-time frontend updates)
    2. Uploaded individually to the backend (streaming, not batched)

    Args:
        state: Current agent state with implementation_plan and file_system.
        config: Runnable configuration.

    Returns:
        State update with populated file_system, build_logs, and files_streamed count.
    """
    logger.info("generation_node: Starting code generation")

    # Get mutable copies
    file_system = dict(state.get("file_system", {}))
    build_logs: List[str] = list(state.get("build_logs", []))
    files_streamed = state.get("files_streamed", 0)

    # Get the implementation plan
    plan = state.get("implementation_plan", [])
    if not plan:
        logger.warning("generation_node: Empty implementation plan")
        build_logs.append("Warning: No tasks in implementation plan")
        return {"file_system": file_system, "build_logs": build_logs, "files_streamed": files_streamed}

    # Get LLM and tools
    llm = get_generation_llm()
    mcp = await get_mcp_wrapper()
    tools = mcp.get_tools()

    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(tools)

    # Get manifest for context
    manifest = state.get("manifest", {})
    manifest_str = json.dumps(manifest, indent=2) if manifest else "No manifest provided"

    # Extract component signatures from existing codebase
    component_signatures = ""
    try:
        analyzer = CodebaseAnalyzer()
        analyzer.analyze_files(file_system)
        component_signatures = analyzer.get_component_signature_string()
        if component_signatures and "No custom components" not in component_signatures:
            logger.info(f"generation_node: Extracted {len(analyzer.components)} component signatures")
    except Exception as e:
        logger.warning(f"generation_node: Could not extract component signatures: {e}")
        component_signatures = ""

    # Get template files to exclude from generation
    template_files = state.get("template_files", {})
    template_paths = set(template_files.keys())

    # Get streaming context from state and config
    org_slug = state.get("org_slug", "")
    project_slug = state.get("project_slug", "")
    thread_id = config.get("configurable", {}).get("thread_id", "")

    # Count generatable tasks (excluding skips/deletes) for progress tracking
    generatable_tasks = [
        t for t in plan
        if t.get("file_path")
        and t.get("type", "create") != "delete"
        and not (t.get("file_path") in template_paths and t.get("type", "create") == "create")
    ]
    total_generatable = len(generatable_tasks)
    generated_index = 0

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

        # Add component inventory if available
        if component_signatures:
            user_content += f"""
{component_signatures}
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
When using components, always verify the required props from the "Available Components" section above.
Never invent component prop signatures - only use components as defined.
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

            # --- Streaming: publish + upload each file immediately ---
            if thread_id:
                await publish_file_generated(
                    thread_id=thread_id,
                    file_path=file_path,
                    content=code,
                    task_index=generated_index,
                    total_tasks=total_generatable,
                )

            if org_slug and project_slug and thread_id:
                uploaded = await stream_file_to_backend(
                    file_path=file_path,
                    content=code,
                    org_slug=org_slug,
                    project_slug=project_slug,
                    job_id=thread_id,
                )
                if uploaded:
                    files_streamed += 1
                    build_logs.append(f"Streamed: {file_path}")

            generated_index += 1

        except Exception as e:
            error_msg = f"Failed to generate {file_path}: {str(e)}"
            logger.error(f"generation_node: {error_msg}")
            build_logs.append(f"Error: {error_msg}")

    logger.info(f"generation_node: Completed. Generated {len(file_system)} files, streamed {files_streamed}.")

    return {
        "file_system": file_system,
        "build_logs": build_logs,
        "files_streamed": files_streamed,
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
# Phase 5: Code Quality Review Node
# =============================================================================

async def code_review_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    Code quality review node — runs between generation and persistence.

    Performs rule-based static analysis on all generated files:
    - Checks for missing 'use client' / 'use server' directives
    - Validates Shadcn import paths
    - Flags accessibility issues (missing alt, aria-label)
    - Detects HTML nesting violations
    - Identifies large barrel imports
    - Auto-fixes simple violations in-place

    Publishes a quality summary to Ably so the frontend can display it.
    Any auto-fixed files are updated in the state's file_system before upload.

    Args:
        state: Current agent state with file_system.
        config: Runnable configuration.

    Returns:
        State update with (possibly patched) file_system and quality_summary.
    """
    from agent.code_quality import CodeReviewer

    logger.info("code_review_node: Running code quality checks")

    file_system = dict(state.get("file_system", {}))
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    build_logs = list(state.get("build_logs", []))

    if not file_system:
        logger.warning("code_review_node: No files to review")
        return {}

    try:
        reviewer = CodeReviewer()
        # auto_fix=True mutates file_system in-place with corrections
        summary = reviewer.review_file_system(file_system, auto_fix=True)

        logger.info(
            f"code_review_node: Review complete — "
            f"score={summary.quality_score}/100, "
            f"errors={summary.total_errors}, warnings={summary.total_warnings}, "
            f"auto_fixes={summary.auto_fixes_applied}"
        )

        # Publish quality summary to Ably
        if thread_id:
            from agent.reflexion import publish_to_ably
            import os as _os
            channel_prefix = _os.getenv("ABLY_CHANNEL_PREFIX", "ai-backend-generation")
            await publish_to_ably(
                f"{channel_prefix}:{thread_id}",
                {
                    "status": "quality_review",
                    "quality_score": summary.quality_score,
                    "total_errors": summary.total_errors,
                    "total_warnings": summary.total_warnings,
                    "auto_fixes_applied": summary.auto_fixes_applied,
                    "files_with_issues": summary.files_with_issues,
                    "message": (
                        f"Code quality: {summary.quality_score}/100 — "
                        f"{summary.total_errors} errors, {summary.total_warnings} warnings"
                        + (f", {summary.auto_fixes_applied} auto-fixed" if summary.auto_fixes_applied else "")
                    ),
                }
            )

        # Add quality log entry
        build_logs.append(
            f"Code quality review: score={summary.quality_score}/100, "
            f"errors={summary.total_errors}, warnings={summary.total_warnings}, "
            f"auto_fixes={summary.auto_fixes_applied}"
        )

        return {
            "file_system": file_system,  # possibly mutated with auto-fixes
            "build_logs": build_logs,
            "quality_summary": summary.to_dict(),
        }

    except Exception as e:
        logger.error(f"code_review_node: Quality review failed: {e}", exc_info=True)
        build_logs.append(f"Code quality review error: {e}")
        return {"build_logs": build_logs}


# =============================================================================
# Node: PersistenceNode (The Uploader)
# =============================================================================

async def persistence_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    The Uploader node - finalizes file uploads to Azure Blob Storage.

    With streaming generation (Phase 1 v2.0), most files are already uploaded
    individually during generation_node via stream_file_to_backend(). This node
    now serves as a finalization step that:
    1. Uploads any files that were NOT streamed (fallback for failures)
    2. Publishes the upload_complete event to signal the frontend
    3. Falls back to batch upload if no files were streamed

    Args:
        state: Current agent state with file_system and files_streamed.
        config: Runnable configuration with thread_id.

    Returns:
        State update with build_ready flag and build_logs.
    """
    logger.info("persistence_node: Starting finalization")

    # Get mutable build logs
    build_logs: List[str] = list(state.get("build_logs", []))

    # Get the file system
    file_system = state.get("file_system", {})
    if not file_system:
        logger.warning("persistence_node: No files to upload")
        build_logs.append("Warning: No files in file system to upload")
        return {"build_ready": False, "build_logs": build_logs}

    # Get org_slug and project_slug from state
    org_slug = state.get("org_slug")
    project_slug = state.get("project_slug")

    if not org_slug or not project_slug:
        logger.error("persistence_node: Missing org_slug or project_slug in state")
        build_logs.append("Error: Missing organization or project slugs for file upload")
        return {"build_ready": False, "build_logs": build_logs}

    files_streamed = state.get("files_streamed", 0)
    total_files = len(file_system)

    # If all files were already streamed during generation, skip batch upload
    if files_streamed >= total_files:
        logger.info(
            f"persistence_node: All {files_streamed} files already streamed. "
            "Skipping batch upload, publishing upload_complete."
        )
        build_logs.append(f"All {files_streamed} files streamed during generation")

        # Publish upload_complete via Ably
        thread_id = config.get("configurable", {}).get("thread_id", "")
        if thread_id:
            try:
                from ably import AblyRealtime

                api_key = os.getenv("ABLY_API_KEY")
                if api_key:
                    client = AblyRealtime(api_key)
                    channel = client.channels.get(f"ai-backend-generation:{thread_id}")
                    await channel.publish("upload_complete", {
                        "status": "upload_complete",
                        "message": f"All {total_files} files uploaded",
                        "file_count": total_files,
                        "files": [{"path": p, "size": len(c)} for p, c in file_system.items()],
                        "refresh_required": True,
                        "organization_slug": org_slug,
                        "project_slug": project_slug,
                    })
                    await client.close()
                    logger.info("persistence_node: Published upload_complete")
            except Exception as e:
                logger.error(f"persistence_node: Failed to publish upload_complete: {e}")

        return {"build_ready": True, "build_logs": build_logs}

    # Fallback: batch upload files that weren't streamed
    logger.info(
        f"persistence_node: {files_streamed}/{total_files} files streamed. "
        "Falling back to batch upload for remaining files."
    )

    # Get backend URL
    backend_url = os.getenv("BACKEND_URL", "http://localhost:8080")
    webhook_secret = os.getenv("FASTAPI_WEBHOOK_SECRET", "")

    # Prepare files array for backend
    files_payload = [
        {"path": path, "content": content}
        for path, content in file_system.items()
    ]

    logger.info(f"persistence_node: Sending {len(files_payload)} files to backend for {org_slug}/{project_slug}")
    build_logs.append(f"Uploading {len(files_payload)} files to {org_slug}/{project_slug}")

    try:
        import aiohttp

        endpoint = f"{backend_url}/api/v1/agents/upload-files"
        payload = {
            "orgSlug": org_slug,
            "projectSlug": project_slug,
            "files": files_payload,
        }

        headers = {
            "Authorization": f"Bearer {webhook_secret}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, headers=headers, timeout=120) as response:
                if response.status == 200:
                    data = await response.json()
                    result = data.get("data", {})
                    uploaded = result.get("uploaded", 0)
                    total = result.get("total", len(files_payload))

                    logger.info(f"persistence_node: Uploaded {uploaded}/{total} files")
                    build_logs.append(f"Uploaded {uploaded}/{total} files to Azure")

                    # Log any failed files
                    results = result.get("results", [])
                    for r in results:
                        if not r.get("success"):
                            error_msg = f"Failed: {r.get('path')} - {r.get('error')}"
                            logger.warning(error_msg)
                            build_logs.append(error_msg)

                    return {"build_ready": uploaded > 0, "build_logs": build_logs}
                else:
                    text = await response.text()
                    error_msg = f"Backend upload failed with status {response.status}: {text}"
                    logger.error(error_msg)
                    build_logs.append(f"Error: {error_msg}")
                    return {"build_ready": False, "build_logs": build_logs}

    except ImportError:
        logger.error("persistence_node: aiohttp package not installed")
        build_logs.append("Error: aiohttp not installed. Run: pip install aiohttp")
        return {"build_ready": False, "build_logs": build_logs}

    except Exception as e:
        error_msg = f"Backend upload failed: {str(e)}"
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
