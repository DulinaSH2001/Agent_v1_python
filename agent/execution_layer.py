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
from agent.ably_utils import publish_tool_usage, publish_task_progress
from agent.code_validator import validate_file, validate_typescript_advanced, validate_security

import asyncio
import json
import logging
import os
import re
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

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# Generation Guard Rails
# =============================================================================

class GenerationGuardRails:
    """
    Enforces constraints during code generation to prevent:
    - Oversized files
    - Excessive complexity
    - Security vulnerabilities
    - Poor code quality
    """

    # File size limits (in characters)
    MAX_FILE_SIZE = 8000  # ~8KB per file
    MAX_TOTAL_SIZE = 150000  # ~150KB total project

    # Complexity limits
    MAX_NESTING_DEPTH = 6
    MAX_FUNCTION_LENGTH = 200  # lines
    MAX_FILE_COUNT = 60

    @staticmethod
    def check_file_size(file_path: str, content: str) -> Optional[str]:
        """Check if file exceeds size limits."""
        size = len(content)
        if size > GenerationGuardRails.MAX_FILE_SIZE:
            return f"File too large ({size} chars, max {GenerationGuardRails.MAX_FILE_SIZE}). Split into smaller modules."
        return None

    @staticmethod
    def check_total_size(file_system: Dict[str, str]) -> Optional[str]:
        """Check if total project size is reasonable."""
        total = sum(len(content) for content in file_system.values())
        if total > GenerationGuardRails.MAX_TOTAL_SIZE:
            return f"Project too large ({total} chars, max {GenerationGuardRails.MAX_TOTAL_SIZE})"
        return None

    @staticmethod
    def check_nesting_depth(content: str) -> Optional[str]:
        """Check for excessive nesting (code complexity)."""
        lines = content.split('\n')
        max_indent = 0
        for line in lines:
            # Skip empty lines and comments
            stripped = line.strip()
            if not stripped or stripped.startswith('//') or stripped.startswith('*'):
                continue

            indent = len(line) - len(line.lstrip())
            if indent > max_indent:
                max_indent = indent

        depth = max_indent // 2  # Assuming 2-space indentation
        if depth > GenerationGuardRails.MAX_NESTING_DEPTH:
            return f"Excessive nesting depth ({depth}, max {GenerationGuardRails.MAX_NESTING_DEPTH}). Extract functions/components."
        return None

    @staticmethod
    def validate_generation(
        file_path: str,
        content: str,
        file_system: Dict[str, str]
    ) -> List[str]:
        """Run all guard rail checks."""
        errors = []

        if err := GenerationGuardRails.check_file_size(file_path, content):
            errors.append(err)

        if err := GenerationGuardRails.check_total_size(file_system):
            errors.append(err)

        if err := GenerationGuardRails.check_nesting_depth(content):
            errors.append(err)

        return errors


def clean_generated_code(content: str) -> str:
    """
    Clean and normalize generated code.

    Removes:
    - Markdown code blocks
    - Placeholder comments like "// ... existing code ..."
    - Extra whitespace
    - Normalizes line endings

    Args:
        content: Raw generated code

    Returns:
        Cleaned code
    """
    # Remove markdown code blocks
    if content.startswith("```"):
        lines = content.split('\n')
        # Remove first line (```typescript, ```tsx, etc.)
        if lines:
            lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = '\n'.join(lines)

    # Remove placeholder comments
    placeholder_patterns = [
        r'//\s*\.\.\.\s*existing\s*code\s*\.\.\..*$',
        r'//\s*\.\.\.\s*rest\s*of\s*(the\s*)?code\s*\.\.\..*$',
        r'//\s*TODO:.*$',
        r'/\*\s*\.\.\.\s*existing\s*code\s*\.\.\.\s*\*/',
        r'/\*\s*\.\.\.\s*rest\s*of\s*(the\s*)?code\s*\.\.\.\s*\*/',
    ]

    for pattern in placeholder_patterns:
        content = re.sub(pattern, '', content,
                         flags=re.IGNORECASE | re.MULTILINE)

    # Normalize line endings
    content = content.replace('\r\n', '\n')

    # Remove trailing whitespace from each line
    lines = [line.rstrip() for line in content.split('\n')]
    content = '\n'.join(lines)

    # Remove multiple consecutive blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)

    # Ensure single trailing newline
    content = content.rstrip() + '\n'

    return content


# =============================================================================
# System Prompts for Code Generation
# =============================================================================

BUILDER_PROMPT = """You are the Builder, an elite frontend engineer with deep expertise in Next.js 15 (React 19), TypeScript, and modern web development patterns.

## Your Mission
Generate production-ready, type-safe, performant Next.js 15 code based on:
1. Implementation task specifications (from Architect)
2. Backend API manifest (data types, endpoints)
3. Existing file content (for delta modifications)
4. Best practices for React 19 and Next.js 15

## 🚫 PROHIBITED FILES - DO NOT GENERATE

**NEVER create these configuration files** (Next.js 15 has built-in defaults):

❌ `.babelrc` - Conflicts with SWC compiler (required for `next/font`)
❌ `.babelrc.js` - Babel not needed in Next.js 15
❌ `babel.config.js` - Use Next.js built-in SWC compiler
❌ `webpack.config.js` - Next.js handles webpack configuration
❌ `.eslintrc` with custom parser - Use Next.js default ESLint config

**Why?**
- Next.js 15 uses SWC compiler by default (faster than Babel)
- `next/font` optimization REQUIRES SWC (breaks with Babel)
- Custom Babel configs cause: "next/font requires SWC although Babel is being used"
- Template already includes all necessary configuration

**Allowed Configuration Files:**
✅ `next.config.mjs` - Only modify if specifically requested
✅ `tailwind.config.js` - Already in template
✅ `tsconfig.json` - Already in template
✅ `components.json` - For Shadcn UI

## CRITICAL: Next.js 15 & React 19 Compliance

### 1. React 19 Features & Patterns

**React Compiler** (Automatic Optimization)
- Write clean code - compiler handles memoization automatically
- NO need for `useMemo`, `useCallback`, `memo` in most cases
- Focus on readability over manual optimization

**Server Components** (Default)
```typescript
// This is a Server Component by default
export default async function UserDashboard() {
  // Direct data fetching
  const users = await db.user.findMany();
  
  return (
    <div>
      <h1>Users ({users.length})</h1>
      <UserList users={users} />
    </div>
  );
}
```

**Client Components** (Interactive Only)
```typescript
'use client'

import { useState } from 'react';
import { Button } from '@/components/ui/button';

export function Counter() {
  const [count, setCount] = useState(0);
  
  return (
    <Button onClick={() => setCount(count + 1)}>
      Count: {count}
    </Button>
  );
}
```

**use() Hook** (React 19 Suspense)
```typescript
'use client'

import { use, Suspense } from 'react';

function UserProfile({ userPromise }: { userPromise: Promise<User> }) {
  const user = use(userPromise); // Unwrap promise
  return <div>{user.name}</div>;
}

export default function Page() {
  const userPromise = fetchUser();
  return (
    <Suspense fallback={<Skeleton />}>
      <UserProfile userPromise={userPromise} />
    </Suspense>
  );
}
```

### 2. Zod Schema Validation (MANDATORY)

**Define Schemas First**
```typescript
import { z } from 'zod';

// Schema definition
export const UserSchema = z.object({
  id: z.string().uuid(),
  email: z.string().email(),
  name: z.string().min(2).max(100),
  role: z.enum(['admin', 'user', 'guest']),
  createdAt: z.coerce.date(),
  metadata: z.record(z.string()).optional(),
});

// Type inference
export type User = z.infer<typeof UserSchema>;

// Validation
const result = UserSchema.safeParse(data);
if (!result.success) {
  console.error(result.error.flatten());
}
```

**Form Schemas with Refinements**
```typescript
const SignUpSchema = z.object({
  email: z.string().email('Invalid email address'),
  password: z.string()
    .min(8, 'Password must be at least 8 characters')
    .regex(/[A-Z]/, 'Must contain uppercase letter')
    .regex(/[0-9]/, 'Must contain number'),
  confirmPassword: z.string(),
}).refine((data) => data.password === data.confirmPassword, {
  message: "Passwords don't match",
  path: ['confirmPassword'],
});
```

### 3. Server Actions (REQUIRED for mutations)

**File Structure**
```typescript
// lib/actions/users.ts
'use server'

import { revalidatePath, revalidateTag } from 'next/cache';
import { redirect } from 'next/navigation';
import { z } from 'zod';
import { db } from '@/lib/db';

const CreateUserSchema = z.object({
  email: z.string().email(),
  name: z.string().min(2),
});

export async function createUser(formData: FormData) {
  // 1. Validate input
  const parsed = CreateUserSchema.safeParse({
    email: formData.get('email'),
    name: formData.get('name'),
  });
  
  if (!parsed.success) {
    return {
      success: false,
      errors: parsed.error.flatten().fieldErrors,
    };
  }
  
  try {
    // 2. Business logic
    const user = await db.user.create({
      data: parsed.data,
    });
    
    // 3. Revalidate cache
    revalidatePath('/users');
    revalidateTag('users-list');
    
    // 4. Optional redirect
    redirect(`/users/${user.id}`);
    
  } catch (error) {
    return {
      success: false,
      error: 'Failed to create user',
    };
  }
}
```

**Progressive Enhancement Pattern**
```typescript
'use client'

import { useFormStatus } from 'react-dom';
import { createUser } from '@/lib/actions/users';

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" disabled={pending}>
      {pending ? 'Creating...' : 'Create User'}
    </Button>
  );
}

export function CreateUserForm() {
  return (
    <form action={createUser}>
      <Input name="email" type="email" required />
      <Input name="name" required />
      <SubmitButton />
    </form>
  );
}
```

### 4. Data Caching Strategies

**`use cache` Directive**
```typescript
import { unstable_cache } from 'next/cache';

// Option 1: Function-level caching
async function getExpensiveData() {
  'use cache';
  const data = await fetch('https://api.example.com/data');
  return data.json();
}

// Option 2: unstable_cache wrapper
const getCachedUser = unstable_cache(
  async (id: string) => {
    return db.user.findUnique({ where: { id } });
  },
  ['user-by-id'],
  { revalidate: 3600, tags: ['users'] }
);
```

**Request Memoization**
```typescript
import { cache } from 'react';

// Deduplicate requests in single render
const getUser = cache(async (id: string) => {
  return db.user.findUnique({ where: { id } });
});
```

### 5. Shadcn UI Best Practices

**Form with React Hook Form + Zod**
```typescript
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';

const formSchema = z.object({
  email: z.string().email(),
  name: z.string().min(2),
});

export function UserForm() {
  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: { email: '', name: '' },
  });
  
  async function onSubmit(values: z.infer<typeof formSchema>) {
    const result = await createUser(values);
    if (result.success) {
      toast.success('User created!');
      form.reset();
    } else {
      toast.error(result.error || 'Failed to create user');
    }
  }
  
  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input placeholder="user@example.com" {...field} />
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

### 6. TypeScript Excellence

**No `any` Types - Use Proper Types**
```typescript
// BAD
function process(data: any) { }

// GOOD
interface ProcessData {
  id: string;
  value: number;
}
function process(data: ProcessData) { }

// BETTER - Generic
function process<T extends { id: string }>(data: T): T { }
```

**Utility Types**
```typescript
type UserInput = Omit<User, 'id' | 'createdAt'>;
type PartialUser = Partial<User>;
type RequiredUser = Required<User>;
type UserKeys = keyof User;
type UserValues = User[keyof User];
```

### 7. Path Aliases (MANDATORY)

**Always use `@/` prefix:**
```typescript
// CORRECT
import { Button } from '@/components/ui/button';
import { db } from '@/lib/db';
import { UserSchema } from '@/types/user';

// WRONG - Never use relative paths
import { Button } from '../../../components/ui/button';
```

### 8. Error Handling Patterns

**Error Boundaries**
```typescript
// app/dashboard/error.tsx
'use client'

import { Button } from '@/components/ui/button';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center min-h-screen">
      <h2 className="text-2xl font-bold mb-4">Something went wrong!</h2>
      <p className="text-muted-foreground mb-4">{error.message}</p>
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
```

### 9. Responsive Design (Mobile-First)

```typescript
<div className="
  grid grid-cols-1        // Mobile: 1 column
  md:grid-cols-2          // Tablet: 2 columns  
  lg:grid-cols-3          // Desktop: 3 columns
  gap-4                   // Consistent gap
">
  {items.map(item => <Card key={item.id} />)}
</div>
```

### 10. Accessibility (WCAG 2.1 AA)

```typescript
// Semantic HTML
<nav aria-label="Main navigation">
  <ul>
    <li><a href="/">Home</a></li>
  </ul>
</nav>

// ARIA labels
<Button aria-label="Close dialog" onClick={onClose}>
  <X className="h-4 w-4" />
</Button>

// Form accessibility
<Label htmlFor="email">Email Address</Label>
<Input 
  id="email" 
  type="email" 
  aria-describedby="email-error"
  aria-invalid={!!errors.email}
/>
{errors.email && (
  <span id="email-error" role="alert">
    {errors.email.message}
  </span>
)}
```

## Output Requirements

### Critical Rules
1. **Return ONLY code** - No markdown, no explanations, no comments outside code
2. **Start with directives** - `'use client'` or `'use server'` if needed (first line)
3. **Complete files** - Entire file content, not snippets
4. **Import organization** - React imports, 3rd party, local (@/ paths)
5. **Type safety** - All props, functions, variables typed
6. **Error handling** - Try-catch in async operations
7. **Validation** - Zod schemas for all inputs
8. **Accessibility** - ARIA labels, semantic HTML
9. **Responsive** - Mobile-first Tailwind classes
10. **Performance** - Lazy load heavy components

### Code Quality Checklist
✅ No `any` types
✅ All imports use `@/` alias
✅ Zod schema for data validation
✅ Error handling in place
✅ TypeScript strict mode compliant
✅ Accessible (ARIA, semantic HTML)
✅ Responsive design (Tailwind)
✅ Server Components by default
✅ Client Components only when needed
✅ Proper error boundaries
"""

DELTA_GENERATION_INSTRUCTION = """
## CRITICAL: FILE MODIFICATION MODE 🔧

You are **MODIFYING** an existing file, not creating from scratch.

### Modification Strategy

1. **Read & Understand Existing Code**
   - Review ALL imports, types, functions, components
   - Understand the current logic flow
   - Identify existing patterns and conventions
   - Note code style (quotes, semicolons, indentation)

2. **Preserve Existing Structure**
   - Keep ALL existing imports (add new ones)
   - Maintain existing function signatures (unless changing)
   - Preserve existing prop types (unless extending)
   - Keep existing exports
   - Maintain file organization

3. **Surgical Modifications**
   - Add new code in appropriate locations
   - Update only specific sections that need changes
   - Don't refactor working code unnecessarily
   - Maintain backward compatibility

4. **Integration Patterns**

   **Adding New Props:**
   ```typescript
   // Existing interface
   interface ButtonProps {
     label: string;
     onClick: () => void;
   }
   
   // Modified interface (ADD new optional props)
   interface ButtonProps {
     label: string;
     onClick: () => void;
     icon?: React.ReactNode;        // NEW
     variant?: 'primary' | 'secondary';  // NEW
   }
   ```

   **Adding New Functions:**
   ```typescript
   // Existing functions preserved
   function existingFunction() { }
   
   // NEW function added below
   function newHelperFunction() { }
   ```

   **Extending Component Logic:**
   ```typescript
   export function Component() {
     // Existing state
     const [data, setData] = useState([]);
     
     // NEW state added
     const [filter, setFilter] = useState('');
     
     // Existing logic preserved
     useEffect(() => {
       fetchData().then(setData);
     }, []);
     
     // NEW logic added
     const filtered = useMemo(() => 
       data.filter(item => item.name.includes(filter)),
       [data, filter]
     );
     
     return (
       <div>
         {/* Existing JSX preserved */}
         <List items={data} />
         
         {/* NEW element added */}
         <SearchInput value={filter} onChange={setFilter} />
       </div>
     );
   }
   ```

5. **Import Management**
   ```typescript
   // Existing imports (KEEP)
   import { useState } from 'react';
   import { Button } from '@/components/ui/button';
   
   // NEW imports (ADD at appropriate location)
   import { Input } from '@/components/ui/input';  // NEW
   import { searchUsers } from '@/lib/actions/users';  // NEW
   ```

6. **Style Consistency**
   - Match existing quote style (single vs double)
   - Match existing semicolon usage
   - Match existing indentation (spaces/tabs)
   - Follow existing naming conventions
   - Use same comment style if adding comments

### Quality Checks
✅ Did you preserve all existing functionality?
✅ Did you add new imports correctly?
✅ Did you maintain existing code style?
✅ Are new additions integrated smoothly?
✅ Did you avoid unnecessary refactoring?
✅ Is the file still cohesive and readable?
✅ Did you test that existing logic still works?

### What NOT to Do
❌ Don't remove working code
❌ Don't rename existing functions/variables
❌ Don't change working logic
❌ Don't restructure unnecessarily
❌ Don't change code style midway
❌ Don't break existing exports
❌ Don't introduce breaking changes

### Current File Content

The existing file is provided below. Carefully modify it according to the task requirements:
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
    component_name: str = Field(
        description="Name of the Shadcn component (e.g., 'Button', 'Card')")
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
                        logger.error(
                            f"Failed to parse MCP_SERVERS_CONFIG: {e}")

                # Priority 2: Legacy single server config (only if not in servers)
                if not servers:
                    if mcp_url:
                        logger.info(
                            f"Connecting to MCP server via SSE at {mcp_url}")
                        servers["docs_server"] = {
                            "url": mcp_url,
                            "transport": "sse",
                        }
                    elif mcp_command:
                        logger.info(
                            f"Connecting to local MCP server via Stdio: {mcp_command}")
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
                    logger.info(
                        f"Successfully connected to {len(servers)} MCP servers. Found {len(self.tools)} tools.")
                else:
                    logger.warning(
                        "No valid MCP servers configured. Falling back to mocks.")
                    self._setup_mock_tools()

            except Exception as e:
                logger.warning(
                    f"Failed to connect to real MCP server: {e}. Falling back to mocks.")
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
        Intelligently determine if a task should use MCP documentation tools.

        Uses pattern matching and priority scoring to decide when tools add value.

        Args:
            task: Implementation task with description and file path.

        Returns:
            True if task would benefit from MCP tool documentation.
        """
        description = task.get("description", "").lower()
        file_path = task.get("file_path", "").lower()
        category = task.get("category", "").lower()
        requires_shadcn = task.get("requires_shadcn", [])

        # High priority patterns (always use tools)
        high_priority_patterns = [
            "server action",
            "use server",
            "form validation",
            "react hook form",
            "zod schema",
            "data mutation",
            "revalidate",
        ]

        # Medium priority patterns (use tools if in UI context)
        medium_priority_patterns = [
            "shadcn",
            "component",
            "dialog",
            "sheet",
            "popover",
            "dropdown",
            "toast",
            "alert",
        ]

        # Component-specific patterns
        shadcn_components = [
            "button", "card", "input", "label", "form",
            "table", "dialog", "sheet", "select", "checkbox",
            "radio", "switch", "textarea", "avatar", "badge",
            "separator", "skeleton", "tabs", "accordion",
        ]

        # Check high priority patterns first
        for pattern in high_priority_patterns:
            if pattern in description:
                return True

        # Check if Shadcn components are explicitly required
        if requires_shadcn and len(requires_shadcn) > 0:
            return True

        # Check for component-specific patterns
        for comp in shadcn_components:
            if comp in description or comp in file_path:
                return True

        # Check medium priority in UI context
        is_ui_file = "components/ui/" in file_path or "ui/" in file_path
        if is_ui_file:
            for pattern in medium_priority_patterns:
                if pattern in description:
                    return True

        # Check category-based patterns
        if category in ["component", "action", "form"]:
            return True

        # Default to false for simple files
        return False

    def get_relevant_tools(self, task: Dict[str, Any]) -> List[str]:
        """
        Determine which specific MCP tools are relevant for a task.

        Args:
            task: Implementation task details.

        Returns:
            List of tool names that should be used for this task.
        """
        description = task.get("description", "").lower()
        file_path = task.get("file_path", "").lower()
        requires_shadcn = task.get("requires_shadcn", [])
        relevant_tools = []

        # Check for Shadcn component needs
        shadcn_keywords = [
            "button", "card", "input", "form", "table", "dialog",
            "sheet", "select", "checkbox", "radio", "avatar", "badge",
        ]

        if requires_shadcn or any(kw in description or kw in file_path for kw in shadcn_keywords):
            relevant_tools.append("get_shadcn_component")

        # Check for Next.js documentation needs
        nextjs_keywords = [
            "server action", "routing", "data fetching", "caching",
            "use server", "use cache", "revalidate", "middleware",
        ]

        if any(kw in description for kw in nextjs_keywords):
            relevant_tools.append("search_nextjs_docs")

        # Check for React patterns
        react_keywords = [
            "hook", "usestate", "useeffect", "useform", "context",
            "suspense", "error boundary",
        ]

        if any(kw in description for kw in react_keywords):
            relevant_tools.append("search_nextjs_docs")

        return relevant_tools


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

            logger.info(
                f"Using Azure OpenAI for generation: {azure_deployment}")
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=azure_key,
                azure_deployment=azure_deployment,
                api_version=azure_version,
                temperature=temperature,
                streaming=streaming,
            )
        except ImportError:
            logger.warning(
                "AzureChatOpenAI not available, falling back to OpenAI")

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

    # Get thread_id for Ably updates
    thread_id = config.get("configurable", {}).get("thread_id", "job-unknown")

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
    manifest_str = json.dumps(
        manifest, indent=2) if manifest else "No manifest provided"

    # Get template files to exclude from generation
    template_files = state.get("template_files", {})
    template_paths = set(template_files.keys())

    # Process each task
    for i, task in enumerate(plan):
        task_id = task.get("id", f"task-{i}")
        task_type = task.get("type", "create")
        file_path = task.get("file_path", "")
        description = task.get("description", "")

        # Publish progress
        await publish_task_progress(
            job_id=thread_id,
            task_index=i+1,
            total_tasks=len(plan),
            file_path=file_path,
            status="generating"
        )

        if not file_path:
            logger.warning(
                f"generation_node: Task {task_id} has no file_path, skipping")
            build_logs.append(f"Skipped task {task_id}: no file path")
            continue

        # Skip template files - they were already uploaded in Phase 1
        if file_path in template_paths and task_type == "create":
            logger.info(
                f"generation_node: Skipping template file {file_path} (already uploaded)")
            build_logs.append(f"Skipped: {file_path} (from template)")
            continue

        if task_type == "delete":
            # Handle file deletion
            if file_path in file_system:
                del file_system[file_path]
                build_logs.append(f"Deleted: {file_path}")
                logger.info(f"generation_node: Deleted {file_path}")
            continue

        logger.info(
            f"generation_node: Processing {task_id} - {task_type} {file_path}")

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
                # Query relevant MCP documentation tools
                logger.info(f"generation_node: Using MCP tools for {task_id}")

                # Get list of relevant tools for this task
                relevant_tool_names = mcp.get_relevant_tools(task)

                # Shadcn component documentation
                if "get_shadcn_component" in relevant_tool_names:
                    comp_tool = next(
                        (t for t in tools if t.name == "get_shadcn_component"), None)

                    if comp_tool:
                        # Extract component names from task
                        requires_shadcn = task.get("requires_shadcn", [])

                        # Also extract from description
                        all_components = [
                            "button", "card", "input", "label", "form", "table",
                            "dialog", "sheet", "select", "checkbox", "radio",
                            "switch", "textarea", "avatar", "badge", "tabs",
                            "accordion", "popover", "dropdown", "toast",
                        ]

                        found_components = requires_shadcn[:]
                        for comp in all_components:
                            if comp in description.lower() and comp not in found_components:
                                found_components.append(comp)

                        # Query documentation for each component
                        if found_components:
                            components_docs = []
                            # Limit to 3 components
                            for comp in found_components[:3]:
                                try:
                                    await publish_tool_usage(thread_id, "get_shadcn_component", comp)
                                    comp_doc = await comp_tool._arun(comp, include_variants=True)
                                    components_docs.append(comp_doc)
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to get docs for {comp}: {e}")

                            if components_docs:
                                combined_docs = "\n\n".join(components_docs)
                                messages.append(HumanMessage(
                                    content=f"## Shadcn UI Component Reference\n{combined_docs}"))

                # Next.js documentation
                if "search_nextjs_docs" in relevant_tool_names:
                    docs_tool = next(
                        (t for t in tools if t.name == "search_nextjs_docs"), None)

                    if docs_tool:
                        # Determine topic based on task
                        topic_mapping = {
                            "server action": ("server actions", "server-actions"),
                            "use server": ("server actions", "server-actions"),
                            "routing": ("routing", "routing"),
                            "route": ("routing", "routing"),
                            "data fetch": ("data fetching", "data-fetching"),
                            "cache": ("caching", "caching"),
                            "revalidate": ("caching", "caching"),
                        }

                        query_made = False
                        for keyword, (query, topic) in topic_mapping.items():
                            if keyword in description.lower():
                                try:
                                    await publish_tool_usage(thread_id, "search_nextjs_docs", query)
                                    docs = await docs_tool._arun(query, topic)
                                    messages.append(HumanMessage(
                                        content=f"## Next.js Documentation: {query.title()}\n{docs}"))
                                    query_made = True
                                    break
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to get Next.js docs for {query}: {e}")

                        # Fallback: general search if no specific topic matched
                        if not query_made:
                            try:
                                await publish_tool_usage(thread_id, "search_nextjs_docs", "general")
                                docs = await docs_tool._arun(description[:100], None)
                                messages.append(HumanMessage(
                                    content=f"## Next.js Documentation\n{docs}"))
                            except Exception as e:
                                logger.warning(
                                    f"Failed to get general Next.js docs: {e}")

            # Generate the code
            response = await llm.ainvoke(messages, config=config)

            # Extract code from response
            code = response.content.strip()

            # Clean generated code (remove markdown, placeholders, etc.)
            code = clean_generated_code(code)

            # =================================================================
            # GUARD RAILS: Validate generation quality
            # =================================================================

            guard_rail_errors = GenerationGuardRails.validate_generation(
                file_path, code, file_system
            )

            if guard_rail_errors:
                logger.warning(
                    f"generation_node: Guard rail violations for {file_path}")
                for error in guard_rail_errors:
                    build_logs.append(f"⚠️  Guard rail: {file_path} - {error}")

                # If file is too large, add a warning but continue
                # The validation_node will catch it later
                if any("too large" in err.lower() for err in guard_rail_errors):
                    build_logs.append(
                        f"Note: {file_path} may need to be split into smaller files")

            # =================================================================
            # IMMEDIATE VALIDATION: Basic syntax check
            # =================================================================

            is_valid, syntax_error = validate_file(file_path, code)
            if not is_valid:
                logger.error(
                    f"generation_node: Syntax error in {file_path}: {syntax_error}")
                build_logs.append(
                    f"❌ Syntax error in {file_path}: {syntax_error}")
                # Still store the file - validation_node will create fix tasks
                file_system[file_path] = code
                continue

            # =================================================================
            # SECURITY CHECK: Quick security scan
            # =================================================================

            if file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
                security_issues = validate_security(code, file_path)
                critical_security = [
                    issue for issue in security_issues if issue.severity == "ERROR"]

                if critical_security:
                    logger.warning(
                        f"generation_node: Security issues in {file_path}")
                    for issue in critical_security[:3]:  # First 3 issues
                        build_logs.append(
                            f"🔒 Security: {file_path} - {issue.message}")

            # Store in file system
            file_system[file_path] = code
            build_logs.append(f"✅ Generated: {file_path} ({len(code)} bytes)")
            logger.info(f"generation_node: Generated {file_path}")

        except Exception as e:
            error_msg = f"Failed to generate {file_path}: {str(e)}"
            logger.error(f"generation_node: {error_msg}")
            build_logs.append(f"Error: {error_msg}")

    logger.info(
        f"generation_node: Completed. Generated {len(file_system)} files.")

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
        logger.warning(
            "FASTAPI_WEBHOOK_SECRET not set. Cannot authenticate SAS token request.")
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
                        logger.error(
                            f"SAS token request failed: {data.get('error')}")
                        return None
                else:
                    text = await response.text()
                    logger.error(
                        f"SAS token request failed with status {response.status}: {text}")
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

    This node takes all files from state['file_system'] and sends them
    to the backend API for upload to Azure Blob Storage.

    Args:
        state: Current agent state with file_system.
        config: Runnable configuration with thread_id.

    Returns:
        State update with build_ready flag and build_logs.
    """
    logger.info("persistence_node: Starting file upload via backend proxy")

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
        logger.error(
            "persistence_node: Missing org_slug or project_slug in state")
        build_logs.append(
            "Error: Missing organization or project slugs for file upload")
        return {"build_ready": False, "build_logs": build_logs}

    # Get backend URL
    backend_url = os.getenv("BACKEND_URL", "http://localhost:8080")
    webhook_secret = os.getenv("FASTAPI_WEBHOOK_SECRET", "")

    # Prepare files array for backend
    files_payload = [
        {"path": path, "content": content}
        for path, content in file_system.items()
    ]

    logger.info(
        f"persistence_node: Sending {len(files_payload)} files to backend for {org_slug}/{project_slug}")
    build_logs.append(
        f"Uploading {len(files_payload)} files to {org_slug}/{project_slug}")

    try:
        import aiohttp

        endpoint = f"{backend_url}/api/v1/agents/upload-files"
        payload = {
            "orgSlug": org_slug,
            "projectSlug": project_slug,
            "files": files_payload
        }

        headers = {
            "Authorization": f"Bearer {webhook_secret}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, headers=headers, timeout=120) as response:
                if response.status == 200:
                    data = await response.json()
                    result = data.get("data", {})
                    uploaded = result.get("uploaded", 0)
                    total = result.get("total", len(files_payload))

                    logger.info(
                        f"persistence_node: Uploaded {uploaded}/{total} files")
                    build_logs.append(
                        f"✅ Uploaded {uploaded}/{total} files to Azure")

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
        build_logs.append(
            "Error: aiohttp not installed. Run: pip install aiohttp")
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
