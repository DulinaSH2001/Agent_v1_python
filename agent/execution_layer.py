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
from agent.codebase_analyzer import CodebaseAnalyzer
from agent.code_validator import validate_file

import asyncio
import json
import logging
import os
import re
import shutil
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

# Configure logging
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ably REST singleton
# AblyRealtime opens a WebSocket per call (slow + SSL failures on macOS).
# AblyRest uses plain HTTPS and is safe to reuse across async tasks.
# ---------------------------------------------------------------------------
_ably_rest: Optional[Any] = None
_ably_config_warning_logged = False


def _looks_like_ably_api_key(api_key: str) -> bool:
    """Return True when the value matches the expected Ably API key shape."""
    key_name, separator, key_secret = api_key.partition(":")
    return bool(separator and key_name and key_secret and "." in key_name)


def _get_ably_rest_channel(channel_name: str) -> Optional[Any]:
    """Return an Ably REST channel, lazily initializing the singleton."""
    global _ably_rest, _ably_config_warning_logged
    if _ably_rest is None:
        api_key = os.getenv("ABLY_API_KEY")
        if not api_key:
            return None
        if not _looks_like_ably_api_key(api_key):
            if not _ably_config_warning_logged:
                logger.warning(
                    "ABLY_API_KEY does not match the expected Ably API key "
                    "format '<appId>.<keyId>:<secret>'; skipping Ably publish."
                )
                _ably_config_warning_logged = True
            return None
        try:
            from ably import AblyRest
            _ably_rest = AblyRest(
                api_key, use_binary_protocol=False, log_level="WARNING")
        except Exception as exc:
            logger.warning(f"Ably REST init failed: {exc}")
            return None
    return _ably_rest.channels.get(channel_name)


# =============================================================================
# System Prompts for Code Generation
# =============================================================================

BUILDER_PROMPT = """Generate Next.js 15 TypeScript/TSX code.

Use: App Router (app/), Server Components, Server Actions (lib/actions.ts), Zod, Shadcn UI, sonner.
Use pre-built components: Sidebar, Header, PageContainer, DataTable, StatCard, EmptyState from @/components/.

Toast/notification guardrail: NEVER use react-hot-toast or react-toastify.
  - FORBIDDEN: `import { toast } from 'react-hot-toast'`, `import toast from 'react-hot-toast'`, `import { Toaster } from 'react-hot-toast'`
  - REQUIRED: `import { toast } from 'sonner'` → `toast.success('...')` or `toast.error('...')`

Table guardrail: NEVER import @tanstack/react-table directly in page/component files.
  - FORBIDDEN: `import { useReactTable } from '@tanstack/react-table'`, `import { getCoreRowModel } from '@tanstack/react-table'`
  - REQUIRED: `import { DataTable } from '@/components/data/DataTable'` with `TableColumn<T>[]` config from `@/types`

Template contract guardrails:
  - `Sidebar` accepts `navLinks`, NOT `links`.
  - REQUIRED: `<Sidebar navLinks={navLinks} />`
  - FORBIDDEN: `<Sidebar links={navLinks} />`
  - `lib/data.ts` MUST continue exporting both `navLinks` and `socialLinks` even after adding project-specific sample data.
  - FORBIDDEN: deleting, renaming, or replacing those exports with differently named constants if template components still import them.
  - If you extend `lib/data.ts`, append project-specific exports and preserve existing template exports.

Visual-editor source tagging (REQUIRED for every JSX/TSX file):
  - Add `data-edit-id="<file>:<line>:<Component>"` to the ROOT JSX element of every component function
    (function/arrow-function component or default export). `<file>` is the file path from project root
    (e.g. `app/page.tsx`), `<line>` is the line number of the component's first JSX tag, `<Component>`
    is the enclosing component name.
  - This attribute lets the visual editor map clicked DOM nodes back to source files. Do NOT skip it.
  - Example: `<main data-edit-id="app/page.tsx:12:HomePage" className="...">`

CSS import guardrail: The global CSS file lives at styles/globals.css (NOT inside app/).
  - FORBIDDEN: `import '@/app/globals.css'`, `import './globals.css'` (from any app/ file)
  - REQUIRED: Only app/layout.tsx imports CSS as `import '../styles/globals.css'` or `import '@/styles/globals.css'`. No other file should import globals.css.
TypeScript strict mode. No 'any' types. Responsive Tailwind CSS.
For App Router dynamic routes (app/**/[param]/**), use Next.js 15 async params shape:
  - REQUIRED: `params: Promise<{ param: string }>` and `const { param } = await params`
  - FORBIDDEN: legacy sync `params: { param: string }` in server files.
Route groups: app/(group)/path/page.tsx and app/path/page.tsx resolve to the SAME URL.
  - NEVER create both; pick one location only.
  - FORBIDDEN: having app/(shop)/products/[slug]/page.tsx AND app/products/[slug]/page.tsx simultaneously.
Canonical pattern:
```tsx
type PageProps = { params: Promise<{ slug: string }> };

export default async function Page({ params }: PageProps) {
  const { slug } = await params;
  return <div>{slug}</div>;
}
```

ORM / database guardrail: NEVER use Prisma, Drizzle, TypeORM, Sequelize, Mongoose, or any ORM/database client.
  - FORBIDDEN: `import { PrismaClient }`, `import prisma`, `@prisma/client`, `prisma.*.findMany`, `prisma.*.create`, etc.
  - REQUIRED: define all data as exported `const` arrays/objects in `lib/data.ts`. No DB calls, no migrations, no schema files.
  - Canonical data pattern:
    ```ts
    // lib/data.ts
    export const products: Product[] = [
      { id: '1', name: 'Widget', price: 29.99, category: 'Tools' },
    ];
    ```
  - Import directly: `import { products } from '@/lib/data'`

Payment / checkout pages: NEVER use Stripe, PayPal, Braintree, or any external payment SDK.
  - FORBIDDEN: `@stripe/stripe-js`, `@stripe/react-stripe-js`, `loadStripe`, `Elements`, `PaymentElement`, `CardElement`.
  - REQUIRED: build a simple, self-contained payment form using only React state + Shadcn UI + react-hook-form + Zod.
  - The form must collect: cardholder name, card number (16 digits), expiry (MM/YY), CVV (3-4 digits).
  - Validate all fields with Zod. Show inline field errors. Show a loading spinner on submit.
  - On submit call a Server Action in lib/actions.ts that accepts the form data (never log raw card data).
  - No external payment dependencies in "dependencies" — the base template already has react-hook-form and Zod.
Canonical payment form skeleton:
```tsx
'use client';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { processPayment } from '@/lib/actions';

const schema = z.object({
  name: z.string().min(2),
  cardNumber: z.string().regex(/^\\d{16}$/, 'Must be 16 digits'),
  expiry: z.string().regex(/^(0[1-9]|1[0-2])\\/\\d{2}$/, 'MM/YY'),
  cvv: z.string().regex(/^\\d{3,4}$/),
});

export default function PaymentForm() {
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm({ resolver: zodResolver(schema) });
  return (
    <form onSubmit={handleSubmit(processPayment)} className="space-y-4 max-w-md">
      <Input {...register('name')} placeholder="Cardholder name" />
      {errors.name && <p className="text-destructive text-sm">{errors.name.message}</p>}
      {/* repeat for cardNumber, expiry, cvv */}
      <Button type="submit" disabled={isSubmitting}>{isSubmitting ? 'Processing…' : 'Pay now'}</Button>
    </form>
  );
}
```

## Styling Requirements (CRITICAL — follow strictly)

Layout:
- Every page with sidebar: use CSS Grid `grid-cols-[auto_1fr]` or flex layout
- Consistent spacing: `p-6 lg:p-8` on page containers, `space-y-8` between sections
- Max-width content: `max-w-7xl mx-auto` for non-sidebar pages

Cards:
- Always use `<Card>` from shadcn with className `card-interactive` (shorthand for shadow-soft hover:shadow-elevated hover:-translate-y-0.5 transition-all duration-200 rounded-xl border-border/50)
- Card headers: `CardTitle` as `text-lg font-semibold`, `CardDescription` as `text-sm text-muted-foreground`
- Card grids: `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6`

Buttons:
- Primary: `<Button>` default variant — add `hover:shadow-glow hover:-translate-y-px transition-all duration-150`
- Secondary: `<Button variant="outline">` with `hover:bg-accent`
- Icon buttons: `<Button variant="ghost" size="icon">`

Typography:
- Hero headings: `text-4xl sm:text-5xl font-bold tracking-tight` with `text-gradient` class
- Section headings: `text-2xl font-semibold tracking-tight`
- Body text: `text-muted-foreground leading-relaxed`

Animations & Transitions:
- Page wrapper: `animate-fade-in` on main `<div>`
- Cards in grids: `animate-slide-up` with staggered `style={{ animationDelay: `${index * 100}ms` }}`
- Nav/header: `sticky top-0 z-50` with `glass` class for frosted blur
- All interactive elements: `transition-all duration-200`

Backgrounds & Depth:
- Hero sections: `bg-gradient-to-br from-primary/5 via-background to-accent/5` or `section-gradient` class
- Alternating sections: alternate `bg-background` and `bg-muted/30`

Color Usage:
- Semantic tokens: `text-primary`, `bg-primary`, `text-muted-foreground`, `bg-muted`
- Accent highlights: `text-primary` for links, active states, important numbers
- Badges: use shadcn `<Badge>` variants (default, secondary, destructive, outline, success, warning)
- Borders: `border-border` default, `border-primary/20` for accent borders

Forms:
- Wrap in `<Card>` with padding. Label + Input with `space-y-2` per field, `space-y-4` between fields
- Error states: `text-destructive text-sm` below inputs
- Submit: `w-full` on mobile

Canonical page composition example:
```tsx
export default function Page() {
  return (
    <div className="animate-fade-in space-y-8 p-6 lg:p-8">
      <section className="space-y-4">
        <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gradient">Title</h1>
        <p className="text-lg text-muted-foreground max-w-2xl leading-relaxed">Description</p>
      </section>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {items.map((item, i) => (
          <Card key={item.id} className="card-interactive animate-slide-up" style={{ animationDelay: `${i * 100}ms` }}>
            <CardHeader><CardTitle className="text-lg font-semibold">{item.title}</CardTitle></CardHeader>
            <CardContent><Badge variant="secondary">{item.status}</Badge></CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
```

Return ONLY code. No markdown, no explanations.
"""

SAMPLE_DATA_INSTRUCTION = """
## DATA MODE: Sample Data (No Backend)
Generate all data as inline TypeScript constants — no fetch() calls, no API requests, no ORM.
- FORBIDDEN: Prisma, Drizzle, TypeORM, Sequelize, Mongoose — any ORM or DB client.
- Define ALL mock data in lib/data.ts as exported const arrays/objects with realistic values (names, emails, dates, amounts, statuses, IDs)
- Import from lib/data.ts in every page/component that needs data
- Do NOT use fetch(), axios, useQuery, SWR, or any network calls anywhere
- Use TypeScript interfaces that match the intended API shape so switching to real API later is easy
- Populate the full UI with enough sample rows/items so the user sees a complete, realistic design
"""


def hex_to_hsl(hex_color: str) -> str:
    """Convert a hex color string to HSL format for Tailwind/Shadcn CSS variables.

    Args:
        hex_color: Color in hex format, e.g. '#3b82f6' or '3b82f6'.

    Returns:
        HSL string without 'hsl()' wrapper, e.g. '217 91% 60%'.
    """
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16) / 255.0, int(hex_color[2:4],
                                                   16) / 255.0, int(hex_color[4:6], 16) / 255.0
    max_c, min_c = max(r, g, b), min(r, g, b)
    l = (max_c + min_c) / 2.0
    if max_c == min_c:
        h = s = 0.0
    else:
        d = max_c - min_c
        s = d / (2.0 - max_c - min_c) if l > 0.5 else d / (max_c + min_c)
        if max_c == r:
            h = (g - b) / d + (6 if g < b else 0)
        elif max_c == g:
            h = (b - r) / d + 2
        else:
            h = (r - g) / d + 4
        h /= 6.0
    return f"{round(h * 360)} {round(s * 100)}% {round(l * 100)}%"


def get_color_palette_css_instruction(palette: dict) -> str:
    """Build a CSS custom property instruction block from a user-selected palette.

    The returned string tells the LLM exactly which HSL values to set in
    globals.css :root / .dark selectors for Tailwind/Shadcn theming.
    """
    primary_hsl = hex_to_hsl(palette.get("primary", "#3b82f6"))
    secondary_hsl = hex_to_hsl(palette.get("secondary", "#6366f1"))
    accent_hsl = hex_to_hsl(palette.get("accent", "#8b5cf6"))
    background_hsl = hex_to_hsl(palette.get("background", "#ffffff"))
    foreground_hsl = hex_to_hsl(palette.get("foreground", "#171717"))
    primary = palette.get("primary", "#3b82f6")
    accent = palette.get("accent", "#8b5cf6")
    return f"""In `styles/globals.css`, set these CSS custom properties in the `:root` selector:
  --background: {background_hsl};
  --foreground: {foreground_hsl};
  --primary: {primary_hsl};
  --primary-foreground: {foreground_hsl};
  --secondary: {secondary_hsl};
  --accent: {accent_hsl};
  --shadow-glow: 0 0 20px -5px {primary}66;
Use the same values (or appropriate light/dark variants) in the `.dark` selector.
Also apply palette throughout the UI:
- Hero/banner backgrounds: bg-gradient-to-br from-[{primary}]/10 to-[{accent}]/10, or text-gradient on main heading
- Primary buttons: bg-[{primary}] hover:bg-[{primary}]/90 hover:shadow-glow
- Cards: border-[{primary}]/20 hover:border-[{primary}]/40 shadow-soft hover:shadow-elevated"""


def get_real_api_instruction(api_base_url: str = None) -> str:
    """Return the REAL_API_INSTRUCTION with the correct base URL."""
    if api_base_url:
        base_url_line = f"- Base URL: `{api_base_url}` — hardcode this as the default in lib/api.ts and also expose it as NEXT_PUBLIC_API_URL in .env.local"
    else:
        base_url_line = "- Base URL: read from `process.env.NEXT_PUBLIC_API_URL` (add to .env.local)"
    return f"""
## DATA MODE: Real API Integration
Connect to real backend API endpoints as defined in the manifest.
- Create typed fetch functions in `lib/api.ts` for every manifest endpoint
{base_url_line}
- Use proper loading states, error handling, and empty states
- Parse responses with Zod for runtime type safety
- Add authentication headers if the manifest specifies auth
"""


DELTA_GENERATION_INSTRUCTION = """
## Modification Mode

You are modifying an existing file. Please follow these rules:

1. **Preserve Existing Code**: Keep all existing imports, types, and logic that are not being changed
2. **Merge Carefully**: Add new functionality without breaking existing features
3. **Maintain Style**: Match the existing code style and patterns
4. **Update Imports**: Add new imports at the top with existing ones
5. **Return Complete File**: Output the entire modified file, not just changes

The current file content is provided below. Modify it according to the task description.
"""


# =============================================================================
# Template Injection — Direct source injection for known pre-built components
# =============================================================================

# Maps keywords (lowercase) in task descriptions → template file paths.
# When a task mentions one of these keywords, the template file source is injected
# directly into the prompt so the LLM has the exact interface to import.
TEMPLATE_INJECTION_MAP: dict = {
    "sidebar": "components/layout/Sidebar.tsx",
    "header bar": "components/layout/Header.tsx",
    "top header": "components/layout/Header.tsx",
    "header": "components/layout/Header.tsx",
    "page container": "components/layout/PageContainer.tsx",
    "data table": "components/data/DataTable.tsx",
    "datatable": "components/data/DataTable.tsx",
    "stat card": "components/data/StatCard.tsx",
    "statcard": "components/data/StatCard.tsx",
    "kpi card": "components/data/StatCard.tsx",
    "kpi metric": "components/data/StatCard.tsx",
    "empty state": "components/data/EmptyState.tsx",
    "emptystate": "components/data/EmptyState.tsx",
}


def _load_corrections() -> list:
    """Load known pitfall corrections from template_corrections.json (cached per process)."""
    try:
        import json as _json
        from pathlib import Path as _Path
        corrections_path = _Path(__file__).parent / "template_corrections.json"
        if not corrections_path.exists():
            return []
        return _json.loads(corrections_path.read_text()).get("corrections", [])
    except Exception:
        return []


# Module-level cache for corrections (loaded once per process)
_corrections_cache: list | None = None


def _get_relevant_corrections(description: str) -> str:
    """
    Return a prompt section with pitfall warnings relevant to the task description.
    Only injects if 2+ corrections match (reduces noise and jailbreak detection).
    Loaded once and cached for the process lifetime.
    """
    global _corrections_cache
    if _corrections_cache is None:
        _corrections_cache = _load_corrections()

    desc_lower = description.lower()
    relevant = [
        c for c in _corrections_cache
        if any(kw in desc_lower for kw in c.get("trigger_keywords", []))
    ]

    # Inject if any corrections are relevant
    if len(relevant) < 1:
        return ""

    lines = [
        f"- [{c['id']}] Preferred: {c['fix']} (instead of {c.get('pitfall', 'common alternative')})"
        for c in relevant
    ]
    return "## Preferred Patterns\n" + "\n".join(lines) + "\n"


def _get_template_injection(description: str, template_files: dict) -> str:
    """
    Return a prompt section with the actual template source for any pre-built
    components mentioned in the task description.
    """
    desc_lower = description.lower()
    seen_paths: set = set()
    injections: list = []

    for keyword, path in TEMPLATE_INJECTION_MAP.items():
        if keyword in desc_lower and path in template_files and path not in seen_paths:
            seen_paths.add(path)
            injections.append(
                f"## Pre-built Component: `{path}`\nImport and use this component directly.\n"
                f"```tsx\n{template_files[path]}\n```"
            )

    if not injections:
        return ""
    return "\n\n".join(injections) + "\n"


# =============================================================================
# Dependency Resolution
# =============================================================================

# Common frontend packages with pinned versions
_KNOWN_PACKAGE_VERSIONS: dict[str, str] = {
    "recharts": "^2.15.0",
    "@tanstack/react-query": "^5.62.0",
    "@tanstack/react-table": "^8.21.0",
    "axios": "^1.7.9",
    "date-fns": "^4.1.0",
    "dayjs": "^1.11.13",
    "framer-motion": "^11.15.0",
    "@dnd-kit/core": "^6.3.1",
    "@dnd-kit/sortable": "^10.0.0",
    "react-icons": "^5.4.0",
    "embla-carousel-react": "^8.5.1",
    "cmdk": "^1.0.4",
    "vaul": "^1.1.2",
    "input-otp": "^1.4.1",
    "react-day-picker": "^9.4.4",
    "react-resizable-panels": "^2.1.7",
    "@hello-pangea/dnd": "^17.0.0",
    "chart.js": "^4.4.7",
    "react-chartjs-2": "^5.2.0",
    "mapbox-gl": "^3.9.3",
    "react-map-gl": "^7.1.8",
    # @stripe/* intentionally omitted — payment pages use simple forms, no SDK
    "react-pdf": "^9.2.1",
    "react-markdown": "^9.0.3",
    "react-syntax-highlighter": "^15.6.1",
    "zustand": "^5.0.3",
    "jotai": "^2.12.2",
    "swr": "^2.3.0",
    "@auth/core": "^0.37.4",
    "next-auth": "^5.0.0-beta.25",
    "lodash": "^4.17.21",
    "@types/lodash": "^4.17.14",
    "uuid": "^11.0.5",
    "@types/uuid": "^10.0.0",
    "sharp": "^0.33.5",
    "ai": "^4.1.0",
    "@ai-sdk/openai": "^1.1.0",
    "uploadthing": "^7.4.4",
    "@uploadthing/react": "^7.1.5",
    "resend": "^4.1.2",
    "@react-email/components": "^0.0.31",
    "socket.io-client": "^4.8.1",
    "pusher-js": "^8.4.0-rc2",
}


def _resolve_dependencies(
    plan: list[dict],
    template_package_json: str,
) -> tuple[dict[str, str], str | None]:
    """
    Collect dependencies from plan tasks, deduplicate against template,
    and return (new_deps_dict, updated_package_json_content | None).

    Returns None for package_json if no new dependencies are needed.
    """
    import json as _json

    # Collect all requested deps from plan tasks
    requested: set[str] = set()
    for task in plan:
        deps = task.get("dependencies", [])
        if isinstance(deps, list):
            requested.update(d.strip() for d in deps if d.strip())

    if not requested:
        return {}, None

    # Parse existing package.json
    try:
        pkg = _json.loads(template_package_json)
    except Exception:
        pkg = {"dependencies": {}, "devDependencies": {}}

    existing_deps = set(pkg.get("dependencies", {}).keys())
    existing_dev = set(pkg.get("devDependencies", {}).keys())
    all_existing = existing_deps | existing_dev

    # Filter out already-installed packages
    new_deps = {
        dep: _KNOWN_PACKAGE_VERSIONS.get(dep, "latest")
        for dep in requested
        if dep not in all_existing
    }

    if not new_deps:
        return {}, None

    # Update package.json
    if "dependencies" not in pkg:
        pkg["dependencies"] = {}
    pkg["dependencies"].update(new_deps)

    updated_json = _json.dumps(pkg, indent=4)
    return new_deps, updated_json


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

        mcp_servers_config_file = os.path.join(
            os.path.dirname(__file__), "..", "mcp_servers_config.json"
        )
        mcp_servers_config = os.getenv("MCP_SERVERS_CONFIG")
        mcp_url = os.getenv("MCP_DOCS_SERVER_URL")
        mcp_command = os.getenv("MCP_DOCS_SERVER_COMMAND")

        if not mcp_servers_config and os.path.exists(mcp_servers_config_file):
            try:
                with open(mcp_servers_config_file, "r", encoding="utf-8") as file:
                    mcp_servers_config = file.read()
                logger.info(
                    f"Loaded MCP server config from {mcp_servers_config_file}"
                )
            except OSError as e:
                logger.warning(
                    f"Failed to read MCP server config file {mcp_servers_config_file}: {e}"
                )

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

                            if "command" in config and shutil.which(config["command"]) is None:
                                logger.warning(
                                    f"Skipping MCP server '{name}': command "
                                    f"'{config['command']}' is not available in the container."
                                )
                                continue

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
                        if shutil.which(cmd_parts[0]) is None:
                            logger.warning(
                                f"Skipping MCP docs server: command '{cmd_parts[0]}' "
                                "is not available in the container."
                            )
                        else:
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
# MCP Context Helpers
# =============================================================================

SHADCN_COMPONENT_HINTS = [
    "alert",
    "alert-dialog",
    "avatar",
    "badge",
    "button",
    "card",
    "checkbox",
    "dialog",
    "dropdown-menu",
    "input",
    "label",
    "progress",
    "scroll-area",
    "select",
    "separator",
    "sheet",
    "skeleton",
    "switch",
    "table",
    "tabs",
    "textarea",
    "tooltip",
]


async def publish_tool_use(
    thread_id: str,
    phase: str,
    tool_name: str,
    message: str,
    *,
    success: bool,
    args: Optional[Dict[str, Any]] = None,
    warning: Optional[str] = None,
    file_path: Optional[str] = None,
    task_index: Optional[int] = None,
) -> bool:
    """Publish a tool_use status update to Ably for frontend visibility."""
    if not thread_id:
        return False

    try:
        channel_prefix = os.getenv(
            "ABLY_CHANNEL_PREFIX", "ai-backend-generation")
        channel_name = f"{channel_prefix}:{thread_id}"
        channel = _get_ably_rest_channel(channel_name)
        if channel is None:
            return False

        payload = {
            "status": "tool_use",
            "phase": phase,
            "tool": tool_name,
            "message": message,
            "success": success,
            "args": args or {},
        }
        if warning:
            payload["warning"] = warning
        if file_path:
            payload["file_path"] = file_path
        if task_index is not None:
            payload["task_index"] = task_index

        await channel.publish("status", payload)
        return True
    except Exception as e:
        logger.warning(f"Failed to publish tool_use event: {e}")
        return False


def infer_shadcn_components_from_text(text: str) -> List[str]:
    """Infer likely shadcn components mentioned in a text."""
    if not text:
        return []

    haystack = text.lower()
    found: List[str] = []
    for comp in SHADCN_COMPONENT_HINTS:
        if comp in haystack:
            found.append(comp)
            continue
        token = comp.replace("-", " ")
        if " " not in token and re.search(rf"\b{re.escape(token)}\b", haystack):
            found.append(comp)

    return sorted(set(found))


# Tools that are action/mutation tools and should NOT be used for documentation retrieval
_TOOL_BLOCKLIST = frozenset({
    "browser_eval",
    "create_or_update_file",
    "nextjs_index",
    "enable_cache_components",
    "puppeteer_navigate",
    "puppeteer_screenshot",
    "puppeteer_click",
    "puppeteer_fill",
    "puppeteer_select",
    "puppeteer_hover",
    "puppeteer_evaluate",
})


def _classify_tool(tool: BaseTool) -> List[str]:
    meta = f"{getattr(tool, 'name', '')} {getattr(tool, 'description', '')}".lower()
    categories: List[str] = []
    if "shadcn" in meta or "component" in meta:
        categories.append("shadcn")
    if "next" in meta or "devtools" in meta or "docs" in meta:
        categories.append("next")
    if "github" in meta:
        categories.append("github")
    if "search" in meta or "brave" in meta:
        categories.append("search")
    return categories


def _tool_field_names(tool: BaseTool) -> List[str]:
    schema = getattr(tool, "args_schema", None)
    if not schema:
        return []
    model_fields = getattr(schema, "model_fields", None)
    if isinstance(model_fields, dict):
        return list(model_fields.keys())
    return []


def _build_tool_payloads(
    tool: BaseTool,
    query: str,
    *,
    component_name: str,
    topic: Optional[str] = None,
) -> List[Any]:
    fields = set(_tool_field_names(tool))
    payloads: List[Any] = []

    if "component_name" in fields:
        payload: Dict[str, Any] = {"component_name": component_name}
        if "include_variants" in fields:
            payload["include_variants"] = True
        payloads.append(payload)

    if fields:
        generic_payload: Dict[str, Any] = {}
        for field in fields:
            if field in {"query", "q", "text", "prompt", "question", "search"}:
                generic_payload[field] = query
            elif field in {"component", "component_name"}:
                generic_payload[field] = component_name
            elif field == "include_variants":
                generic_payload[field] = True
            elif field == "topic" and topic:
                generic_payload[field] = topic
            elif field == "repo":
                generic_payload[field] = "vercel/next.js"
        if generic_payload:
            payloads.append(generic_payload)

    # Only add dict payloads for tools with JSON schema (args_schema)
    # String payloads are not allowed for tools with schema validation
    payloads.extend([{"query": query}, {"q": query}])
    # Only add bare string payload if tool doesn't have args_schema
    if not hasattr(tool, "args_schema") or tool.args_schema is None:
        payloads.append(query)
    return payloads


async def _invoke_tool_best_effort(tool: BaseTool, payloads: List[Any]) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """Try multiple payload shapes to accommodate varied MCP tool schemas.

    Filters out string payloads for tools with JSON schema validation,
    since such tools require dictionary arguments.
    """
    last_error: Optional[Exception] = None

    # Tools with args_schema don't accept string payloads
    has_json_schema = hasattr(
        tool, "args_schema") and tool.args_schema is not None

    for payload in payloads:
        # Skip string payloads for tools with JSON schema
        if not isinstance(payload, dict) and has_json_schema:
            continue

        try:
            if hasattr(tool, "ainvoke"):
                result = await tool.ainvoke(payload)
            elif isinstance(payload, dict):
                # type: ignore[attr-defined]
                result = await tool._arun(**payload)
            else:
                # type: ignore[attr-defined]
                result = await tool._arun(payload)

            used_args = payload if isinstance(payload, dict) else {
                "query": str(payload)}
            return str(result), None, used_args
        except Exception as e:
            last_error = e

    return None, str(last_error) if last_error else "Unknown tool invocation failure", None


async def gather_mcp_context(
    *,
    phase: str,
    prompt: str,
    manifest: Optional[Dict[str, Any]] = None,
    task: Optional[Dict[str, Any]] = None,
    error_text: Optional[str] = None,
    thread_id: str = "",
    file_path: Optional[str] = None,
    task_index: Optional[int] = None,
    max_references: int = 4,
) -> Dict[str, Any]:
    """
    Collect MCP context snippets for a phase and emit tool_use events.
    Continues on MCP/tool failures.
    """
    task = task or {}
    query = " ".join(
        chunk
        for chunk in [
            prompt.strip() if prompt else "",
            task.get("description", "").strip(),
            error_text.strip() if error_text else "",
        ]
        if chunk
    )[:1200]

    try:
        mcp = await get_mcp_wrapper()
        all_tools = mcp.get_tools()
        # Filter out action/mutation tools that shouldn't be used for documentation lookups
        tools = [t for t in all_tools if getattr(
            t, "name", "") not in _TOOL_BLOCKLIST]
    except Exception as e:
        warning = f"MCP initialization failed: {e}"
        logger.warning(warning)
        await publish_tool_use(
            thread_id=thread_id,
            phase=phase,
            tool_name="mcp_init",
            message=warning,
            success=False,
            warning=warning,
            file_path=file_path,
            task_index=task_index,
        )
        return {"references": [], "tools_used": [], "warnings": [warning], "mcp_available": False}

    if not tools:
        warning = "No MCP tools available"
        await publish_tool_use(
            thread_id=thread_id,
            phase=phase,
            tool_name="mcp_tools",
            message=warning,
            success=False,
            warning=warning,
            file_path=file_path,
            task_index=task_index,
        )
        return {"references": [], "tools_used": [], "warnings": [warning], "mcp_available": mcp.mcp_available}

    required_categories = {"shadcn", "next"}
    error_sensitive_text = f"{query} {error_text or ''}".lower()
    if phase in {"reflexion", "code_review"} or "error" in error_sensitive_text or "failed" in error_sensitive_text:
        required_categories.update({"github", "search"})

    picked_tools: List[BaseTool] = []
    for category in ["shadcn", "next", "github", "search"]:
        if category not in required_categories:
            continue
        for tool in tools:
            if category in _classify_tool(tool):
                picked_tools.append(tool)
                break

    if not picked_tools:
        picked_tools = tools[:2]

    deduped_tools: List[BaseTool] = []
    seen_names = set()
    for tool in picked_tools:
        name = getattr(tool, "name", str(tool))
        if name in seen_names:
            continue
        seen_names.add(name)
        deduped_tools.append(tool)

    inferred_components = infer_shadcn_components_from_text(query)
    primary_component = inferred_components[0] if inferred_components else "button"
    references: List[str] = []
    tools_used: List[str] = []
    warnings: List[str] = []

    # ── run all MCP tool invocations concurrently ────────────────────────────
    async def _run_one_tool(tool: BaseTool):
        t_name = getattr(tool, "name", "unknown_tool")
        t_cats = _classify_tool(tool)
        t_topic = "server-actions" if "action" in query.lower() else "routing"
        if "shadcn" in t_cats:
            t_topic = "components"
        elif "github" in t_cats:
            t_topic = "known-bugs"
        t_payloads = _build_tool_payloads(
            tool,
            query or prompt or "nextjs shadcn best practices",
            component_name=primary_component,
            topic=t_topic,
        )
        await publish_tool_use(
            thread_id=thread_id, phase=phase, tool_name=t_name,
            message=f"Using MCP tool {t_name}", success=True,
            args={"topic": t_topic}, file_path=file_path, task_index=task_index,
        )
        t_result, t_error, t_used_args = await _invoke_tool_best_effort(tool, t_payloads)
        return t_name, t_topic, t_result, t_error, t_used_args

    tool_outputs = await asyncio.gather(
        *[_run_one_tool(t) for t in deduped_tools],
        return_exceptions=True,
    )

    for output in tool_outputs:
        if isinstance(output, BaseException):
            warnings.append(f"Tool raised unexpectedly: {output}")
            continue
        tool_name, _topic, result, error, used_args = output
        if result:
            tools_used.append(tool_name)
            excerpt = result.strip()
            if len(excerpt) > 1200:
                excerpt = excerpt[:1200] + "..."
            references.append(f"[{tool_name}] {excerpt}")
            await publish_tool_use(
                thread_id=thread_id, phase=phase, tool_name=tool_name,
                message=f"MCP tool {tool_name} completed", success=True,
                args=used_args or {}, file_path=file_path, task_index=task_index,
            )
        else:
            warning = f"{tool_name} unavailable: {error or 'Unknown error'}"
            warnings.append(warning)
            logger.warning(f"gather_mcp_context: {warning}")
            await publish_tool_use(
                thread_id=thread_id, phase=phase, tool_name=tool_name,
                message=warning, success=False, warning=warning,
                args=used_args or {}, file_path=file_path, task_index=task_index,
            )
    # ── end parallel MCP block ───────────────────────────────────────────────

    return {
        "references": references[:max_references],
        "tools_used": sorted(set(tools_used)),
        "warnings": warnings,
        "mcp_available": mcp.mcp_available,
    }


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
    azure_deployment = os.getenv(
        "AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.3-chat")
    azure_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")

    # gpt-5.3-chat only supports temperature=1 (default)
    if "gpt-5.3" in azure_deployment.lower():
        temperature = 1.0

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
        model="gpt-5.3-chat",
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
        channel = _get_ably_rest_channel(f"ai-backend-generation:{thread_id}")
        if channel is None:
            logger.warning(
                "Ably not configured, skipping file_generated publish")
            return False

        await channel.publish("file_generated", {
            "file_path": file_path,
            "content": content,
            "content_size": len(content),
            "task_index": task_index,
            "total_tasks": total_tasks,
            "progress": int(((task_index + 1) / total_tasks) * 100) if total_tasks else 0,
            "message": f"Generated {file_path} ({task_index + 1}/{total_tasks})",
        })

        logger.info(
            f"Published file_generated for {file_path} ({task_index + 1}/{total_tasks})")
        return True

    except ImportError:
        logger.warning(
            "Ably package not installed, skipping file_generated publish")
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

        logger.debug(f"Streaming file to {endpoint} ({org_slug}/{project_slug}/{file_path})")

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, headers=headers, timeout=30) as response:
                if response.status == 200:
                    logger.info(f"Streamed file to backend: {file_path}")
                    return True
                else:
                    text = await response.text()
                    logger.error(
                        f"Failed to stream {file_path}: status {response.status}, "
                        f"endpoint={endpoint}, body={text[:200]}")
                    return False

    except ImportError:
        logger.error("aiohttp not installed, cannot stream file")
        return False
    except Exception as e:
        logger.error(
            f"Failed to stream file {file_path}: {type(e).__name__}: {e!r}, "
            f"BACKEND_URL={backend_url}")
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
            modification_request = state.get(
                "user_message", state.get("query", ""))
            affected_files = analyzer.find_affected_files(
                modification_request, state["file_system"])

            # Get codebase summary for LLM context
            codebase_summary = analyzer.get_file_summary()

            # Analyze impact of each affected file
            file_impacts = {
                file_path: analyzer.analyze_file_impact(file_path)
                for file_path in affected_files
            }

            logger.info(f"Found {len(affected_files)} affected files")
            logger.info(
                f"File impacts: {json.dumps(file_impacts, default=str, indent=2)}")

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
        thread_id = config.get("configurable", {}).get(
            "thread_id", "") if config else ""
        modification_request = state.get(
            "user_message", state.get("query", ""))

        mcp_context = await gather_mcp_context(
            phase="modification",
            prompt=modification_request,
            manifest=state.get("manifest", {}),
            task={"description": modification_request,
                  "file_path": ",".join(affected_files[:2])},
            thread_id=thread_id,
            max_references=2,
        )

        if not affected_files:
            logger.warning("No affected files to plan modifications for")
            return {
                "delta_mode": True,
                "modification_targets": [],
            }

        # Build modification instruction for the generator
        modification_targets = []
        # Limit to top 5 to avoid token explosion
        for file_path in affected_files[:5]:
            file_impact = analysis.get("file_impacts", {}).get(file_path, {})
            scope = file_impact.get("impact_scope", 0)

            modification_targets.append({
                "file_path": file_path,
                "operation": "modify",  # vs "create" or "delete"
                "scope": scope,
                # Top 3 dependents
                "dependencies": file_impact.get("imported_by", [])[:3],
                "mcp_tools_used": mcp_context.get("tools_used", []),
                "requires_shadcn": infer_shadcn_components_from_text(modification_request),
            })

        logger.info(
            f"Created modification targets for {len(modification_targets)} files")

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

    # Validate and correct plan operations (misclassified ops)
    from agent.file_ops import validate_plan_operations
    plan, op_warnings = validate_plan_operations(plan, file_system)
    for w in op_warnings:
        build_logs.append(f"Plan correction: {w}")

    # Rule-based plan pre-validation (before any LLM calls)
    try:
        from agent.plan_validator import validate_plan as _validate_plan
        _template_files = state.get("template_files", {})
        plan, plan_warnings = _validate_plan(
            plan, file_system, _template_files)
        for w in plan_warnings:
            build_logs.append(f"Plan validator: {w}")
    except Exception as _pve:
        logger.warning(f"generation_node: Plan pre-validation skipped: {_pve}")

    # Resolve dependencies from plan tasks and update package.json
    resolved_deps: Dict[str, str] = {}
    try:
        template_pkg = state.get("template_files", {}).get("package.json", "")
        if not template_pkg:
            # Try from file_system
            template_pkg = file_system.get("package.json", "{}")
        resolved_deps, updated_pkg_json = _resolve_dependencies(
            plan, template_pkg)
        if resolved_deps and updated_pkg_json:
            file_system["package.json"] = updated_pkg_json
            build_logs.append(
                f"Dependencies added: {', '.join(resolved_deps.keys())}")
            logger.info(
                f"generation_node: Resolved {len(resolved_deps)} new dependencies: {list(resolved_deps.keys())}")
    except Exception as _dep_err:
        logger.warning(
            f"generation_node: Dependency resolution failed: {_dep_err}")

    # Get LLM
    llm = get_generation_llm()

    # Get manifest for context
    manifest = state.get("manifest", {})
    manifest_str = json.dumps(
        manifest, indent=2) if manifest else "No manifest provided"

    # Extract component signatures from existing codebase
    component_signatures = ""
    try:
        analyzer = CodebaseAnalyzer()
        analyzer.analyze_files(file_system)
        component_signatures = analyzer.get_component_signature_string()
        if component_signatures and "No custom components" not in component_signatures:
            logger.info(
                f"generation_node: Extracted {len(analyzer.components)} component signatures")
    except Exception as e:
        logger.warning(
            f"generation_node: Could not extract component signatures: {e}")
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

    # Handle deletes first (sequential, no LLM needed)
    for task in plan:
        if task.get("type") == "delete" and task.get("file_path") in file_system:
            del file_system[task["file_path"]]
            build_logs.append(f"Deleted: {task['file_path']}")

    # Pre-fetch RAG context for all tasks in parallel (batch instead of N sequential calls)
    rag_context_map: dict = {}
    try:
        from agent.template_rag import retrieve_relevant_chunks, get_template_rag
        rag_instance = get_template_rag()

        async def _fetch_rag_for_task(t: dict) -> tuple:
            fp = t.get("file_path", "")
            desc = t.get("description", "")
            task_type = t.get("type", "create")
            try:
                # Enhanced query: add file path context for better matching
                query_text = f"{desc} file:{fp}"
                fp_lower = fp.lower()
                if "layout" in fp_lower or "sidebar" in desc.lower() or "header" in desc.lower():
                    query_text += " layout component navigation"
                elif fp.endswith("page.tsx"):
                    query_text += " page component server component"
                elif "data" in fp_lower or "table" in desc.lower():
                    query_text += " data display table"

                chunks = retrieve_relevant_chunks(
                    query=query_text,
                    task_description=f"{task_type} {fp}: {desc}",
                    top_k=4,
                )
                return fp, rag_instance.format_for_prompt(chunks) if chunks else ""
            except Exception:
                return fp, ""

        rag_results = await asyncio.gather(
            *[_fetch_rag_for_task(t) for t in generatable_tasks],
            return_exceptions=True,
        )
        for r in rag_results:
            if isinstance(r, tuple):
                fp, ctx = r
                if ctx:
                    rag_context_map[fp] = ctx
    except Exception as e:
        logger.debug(f"generation_node: Batch RAG prefetch skipped: {e}")

    # MCP context cache — keyed by component hints hash, avoid re-fetching per file
    _mcp_cache_local: dict = {}

    async def _get_mcp_cached(task: dict, file_path: str, task_index: int) -> dict:
        import hashlib
        hints = tuple(sorted(task.get("requires_shadcn", [])))
        cache_key = hashlib.md5(str(hints).encode()).hexdigest()[:8]
        if cache_key in _mcp_cache_local:
            return _mcp_cache_local[cache_key]
        ctx = await gather_mcp_context(
            phase="generation",
            prompt=task.get("description", ""),
            manifest=manifest,
            task=task,
            thread_id=thread_id,
            file_path=file_path,
            task_index=task_index,
            max_references=3,
        )
        _mcp_cache_local[cache_key] = ctx
        return ctx

    # ── Parallel generation with concurrency limit ────────────────────────────
    # Tasks run with max GENERATION_BATCH_SIZE concurrent LLM calls.
    # Results are collected and merged into file_system at the end.
    GENERATION_BATCH_SIZE = 3
    semaphore = asyncio.Semaphore(GENERATION_BATCH_SIZE)

    data_mode = state.get("data_mode", "real_api")
    api_base_url = state.get("api_base_url", None)

    async def _generate_one_task(task: dict, task_index: int) -> tuple:
        """
        Generate code for a single task.
        Returns (file_path, code, logs, streamed_count).
        """
        task_logs: list = []
        task_id = task.get("id", f"task-{task_index}")
        task_type = task.get("type", "create")
        file_path = task.get("file_path", "")
        description = task.get("description", "")

        if not file_path:
            task_logs.append(f"Skipped task {task_id}: no file path")
            return file_path, None, task_logs, 0

        if file_path in template_paths and task_type == "create":
            task_logs.append(f"Skipped: {file_path} (from template)")
            return file_path, None, task_logs, 0

        # Build system prompt
        sys_prompt = BUILDER_PROMPT
        if data_mode == "sample_data":
            sys_prompt += SAMPLE_DATA_INSTRUCTION
        elif manifest:
            sys_prompt += get_real_api_instruction(api_base_url)

        existing_content = ""
        if task_type == "modify" and file_path in file_system:
            existing_content = file_system[file_path]
            sys_prompt += DELTA_GENERATION_INSTRUCTION

        # Inject short template manifest for builder awareness
        _builder_manifest = ""
        try:
            from agent.template_manifest import get_short_manifest_for_builder
            _builder_manifest = get_short_manifest_for_builder()
        except Exception:
            pass

        user_content = f"""## Task
{description}

## File Path
{file_path}

{_builder_manifest}

## Backend API Manifest
```json
{manifest_str}
```
"""
        if component_signatures:
            user_content += f"\n{component_signatures}\n"

        # Inject targeted component hints based on task context
        desc_lower = description.lower()
        fp_lower = file_path.lower()
        _targeted_hints = []
        if any(kw in desc_lower for kw in ("sidebar", "navigation", "nav")):
            if "layout" in fp_lower or "layout" in desc_lower:
                _targeted_hints.append(
                    '```tsx\nimport { Sidebar } from "@/components/layout/Sidebar";\n'
                    'import { Header } from "@/components/layout/Header";\n'
                    'import { PageContainer } from "@/components/layout/PageContainer";\n'
                    'import { navLinks } from "@/lib/data";\n```'
                )
        if any(kw in desc_lower for kw in ("table", "list", "data view", "grid")):
            _targeted_hints.append(
                '```tsx\nimport { DataTable } from "@/components/data/DataTable";\n'
                'import type { TableColumn } from "@/types";\n```'
            )
        if any(kw in desc_lower for kw in ("stat", "metric", "kpi", "overview", "dashboard")):
            _targeted_hints.append(
                '```tsx\nimport { StatCard } from "@/components/data/StatCard";\n```'
            )
        if _targeted_hints:
            user_content += "\n## Suggested Imports\n" + "\n".join(_targeted_hints) + "\n"

        # Use pre-fetched RAG context (no extra Pinecone call per task)
        rag_ctx = rag_context_map.get(file_path, "")
        if rag_ctx:
            user_content += f"\n{rag_ctx}\n"

        # Inject pre-built template source for known component patterns
        template_injection = _get_template_injection(description, template_files)
        if template_injection:
            user_content += f"\n{template_injection}\n"

        if existing_content:
            user_content += f"""
## Current File Content (MODIFY this file)
```typescript
{existing_content}
```
"""
        # Inject known pitfall corrections relevant to this task
        corrections = _get_relevant_corrections(description)
        if corrections:
            user_content += f"\n{corrections}\n"

        user_content += """
## Output
Respond with the complete file content only. No explanations, no markdown fences.
Props for imported components should match the signatures described in the Available Components section.
"""

        async with semaphore:
            try:
                messages = [
                    SystemMessage(content=sys_prompt),
                    HumanMessage(content=user_content),
                ]

                mcp_context = await _get_mcp_cached(task, file_path, task_index)
                if mcp_context["references"]:
                    messages.append(
                        HumanMessage(content="## MCP References\n" +
                                     "\n\n".join(mcp_context["references"]))
                    )
                if mcp_context["warnings"]:
                    task_logs.extend(
                        [f"MCP warning ({file_path}): {w}" for w in mcp_context["warnings"][:2]])
                if mcp_context["tools_used"]:
                    task["mcp_tools_used"] = sorted(
                        set(task.get("mcp_tools_used", []) + mcp_context["tools_used"]))

                response = await llm.ainvoke(messages, config=config)
                code = response.content.strip()

                # Strip markdown fences if present
                if code.startswith("```"):
                    lines = code.split("\n")[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    code = "\n".join(lines)

                # Inline quality auto-fix: run rule checks and apply fixes before publish
                try:
                    from agent.code_quality import CodeReviewer
                    reviewer = CodeReviewer()
                    review = reviewer.review_file(file_path, code, file_system)
                    if review.fixed_content and review.fixed_content != code:
                        fixed_count = sum(
                            1 for i in review.issues if i.fix_applied)
                        code = review.fixed_content
                        task_logs.append(
                            f"Auto-fixed {fixed_count} issue(s) in {file_path}")
                    # If errors remain (max 3), do a single targeted LLM correction call
                    remaining_errors = [
                        i for i in review.issues if i.severity == "error" and not i.fix_applied]
                    if 0 < len(remaining_errors) <= 3:
                        error_lines = "\n".join(
                            f"- {i.rule} (line {i.line}): {i.message}" for i in remaining_errors
                        )
                        messages.append(HumanMessage(
                            content=f"## Fix These Errors\nReturn the COMPLETE corrected file:\n{error_lines}"
                        ))
                        correction = await llm.ainvoke(messages, config=config)
                        corrected = correction.content.strip()
                        if corrected.startswith("```"):
                            corr_lines = corrected.split("\n")[1:]
                            if corr_lines and corr_lines[-1].strip() == "```":
                                corr_lines = corr_lines[:-1]
                            corrected = "\n".join(corr_lines)
                        code = corrected
                        task_logs.append(
                            f"LLM-corrected {len(remaining_errors)} error(s) in {file_path}")
                except Exception as _qe:
                    logger.debug(
                        f"generation_node: Inline quality check skipped for {file_path}: {_qe}")

                task_logs.append(f"Generated: {file_path} ({len(code)} bytes)")
                logger.info(f"generation_node: Generated {file_path}")

                # Publish + upload immediately after generation
                streamed = 0
                if thread_id:
                    await publish_file_generated(
                        thread_id=thread_id,
                        file_path=file_path,
                        content=code,
                        task_index=task_index,
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
                        streamed = 1
                        task_logs.append(f"Streamed: {file_path}")

                return file_path, code, task_logs, streamed

            except Exception as e:
                err_str = str(e)
                is_content_filter = (
                    "content_filter" in err_str
                    or "ResponsibleAIPolicyViolation" in err_str
                    or ("jailbreak" in err_str.lower() and "400" in err_str)
                )

                if not is_content_filter:
                    error_msg = f"Failed to generate {file_path}: {err_str}"
                    logger.error(f"generation_node: {error_msg}")
                    task_logs.append(f"Error: {error_msg}")
                    return file_path, None, task_logs, 0

                # Content filter / jailbreak false-positive — retry with minimal prompt
                logger.warning(
                    f"generation_node: Content filter triggered for {file_path}. "
                    "Retrying with minimal prompt."
                )
                minimal_sys = (
                    "You are a senior Next.js developer using TypeScript and Tailwind CSS. "
                    "Write clean, production-ready code."
                )
                minimal_user = (
                    f"Create the file: {file_path}\n\n"
                    f"Project context: {state.get('user_prompt', '')}\n\n"
                    f"Purpose: {description}\n\n"
                    "Respond with only the file content."
                )
                try:
                    retry_messages = [
                        SystemMessage(content=minimal_sys),
                        HumanMessage(content=minimal_user),
                    ]
                    retry_response = await llm.ainvoke(retry_messages, config=config)
                    code = retry_response.content.strip()
                    if code.startswith("```"):
                        lines = code.split("\n")[1:]
                        if lines and lines[-1].strip() == "```":
                            lines = lines[:-1]
                        code = "\n".join(lines)
                    task_logs.append(
                        f"Generated (retry): {file_path} ({len(code)} bytes)")
                    logger.info(
                        f"generation_node: Retry succeeded for {file_path}")

                    streamed = 0
                    if thread_id:
                        await publish_file_generated(
                            thread_id=thread_id,
                            file_path=file_path,
                            content=code,
                            task_index=task_index,
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
                            streamed = 1
                            task_logs.append(f"Streamed: {file_path}")

                    return file_path, code, task_logs, streamed

                except Exception as retry_err:
                    error_msg = f"Failed to generate {file_path} (retry): {str(retry_err)}"
                    logger.error(f"generation_node: {error_msg}")
                    task_logs.append(f"Error: {error_msg}")
                    return file_path, None, task_logs, 0

    # Run all generatable tasks in parallel (semaphore caps concurrency)
    logger.info(
        f"generation_node: Generating {total_generatable} files with batch_size={GENERATION_BATCH_SIZE}")
    task_results = await asyncio.gather(
        *[_generate_one_task(t, idx)
          for idx, t in enumerate(generatable_tasks)],
        return_exceptions=True,
    )

    for result in task_results:
        if isinstance(result, Exception):
            build_logs.append(f"Error: {result}")
            continue
        fp, code, task_logs, streamed = result
        build_logs.extend(task_logs)
        files_streamed += streamed
        if code is not None:
            file_system[fp] = code

    logger.info(
        f"generation_node: Completed. Generated {len(file_system)} files, streamed {files_streamed}.")

    return {
        "file_system": file_system,
        "build_logs": build_logs,
        "files_streamed": files_streamed,
        "resolved_dependencies": resolved_deps,
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
    thread_id = config.get("configurable", {}).get(
        "thread_id", "") if config else ""
    build_logs = list(state.get("build_logs", []))

    if not file_system:
        logger.warning("code_review_node: No files to review")
        return {}

    try:
        reviewer = CodeReviewer()
        # auto_fix=True mutates file_system in-place with corrections
        summary = reviewer.review_file_system(file_system, auto_fix=True)

        # Stamp data-edit-id attributes on component root JSX so the visual editor
        # can map clicked DOM nodes back to source files. Runs after CodeReviewer
        # so its auto-fixes don't strip our attributes. Idempotent — safe to re-run
        # on already-stamped files (e.g. during modification jobs).
        try:
            from agent.source_tagger import stamp_file_system
            before = dict(file_system)
            file_system = stamp_file_system(file_system)
            stamped_count = sum(
                1 for p, c in file_system.items()
                if before.get(p) != c
            )
            if stamped_count:
                logger.info(
                    f"code_review_node: source_tagger stamped {stamped_count} file(s) with data-edit-id"
                )
        except Exception as tag_err:
            logger.warning(
                f"code_review_node: source_tagger failed (non-fatal): {tag_err}"
            )

        mcp_advisory = await gather_mcp_context(
            phase="code_review",
            prompt=(
                f"Quality score {summary.quality_score}. "
                f"errors={summary.total_errors} warnings={summary.total_warnings}"
            ),
            manifest=state.get("manifest", {}),
            task={"description": "Final quality advisory for generated files"},
            error_text="\n".join(build_logs[-10:]),
            thread_id=thread_id,
            max_references=2,
        )

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
            channel_prefix = _os.getenv(
                "ABLY_CHANNEL_PREFIX", "ai-backend-generation")
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
        if mcp_advisory["warnings"]:
            build_logs.extend(
                [f"MCP advisory warning: {w}" for w in mcp_advisory["warnings"][:2]])

        quality_summary = summary.to_dict()
        quality_summary["mcp_tools_used"] = mcp_advisory.get("tools_used", [])
        quality_summary["mcp_advisory"] = mcp_advisory.get("references", [])

        # Check for unfixed critical errors (blocking gate)
        remaining_critical = sum(
            1 for r in summary.results
            for i in r.issues
            if i.severity == "error" and not i.fix_applied
        )
        quality_summary["blocking"] = remaining_critical > 0
        quality_summary["remaining_critical_errors"] = remaining_critical
        if remaining_critical > 0:
            build_logs.append(
                f"Quality gate: {remaining_critical} unfixed critical error(s) remain"
            )

        return {
            "file_system": file_system,  # possibly mutated with auto-fixes
            "build_logs": build_logs,
            "quality_summary": quality_summary,
            "iteration_count": state.get("iteration_count", 0) + 1,
        }

    except Exception as e:
        logger.error(
            f"code_review_node: Quality review failed: {e}", exc_info=True)
        build_logs.append(f"Code quality review error: {e}")
        return {
            "build_logs": build_logs,
            "iteration_count": state.get("iteration_count", 0) + 1,
        }


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
        logger.error(
            "persistence_node: Missing org_slug or project_slug in state")
        build_logs.append(
            "Error: Missing organization or project slugs for file upload")
        return {"build_ready": False, "build_logs": build_logs}

    files_streamed = state.get("files_streamed", 0)
    total_files = len(file_system)

    # If all files were already streamed during generation, skip batch upload
    if files_streamed >= total_files:
        logger.info(
            f"persistence_node: All {files_streamed} files already streamed. "
            "Skipping batch upload, publishing upload_complete."
        )
        build_logs.append(
            f"All {files_streamed} files streamed during generation")

        # Publish upload_complete via Ably
        thread_id = config.get("configurable", {}).get("thread_id", "")
        if thread_id:
            try:
                channel = _get_ably_rest_channel(
                    f"ai-backend-generation:{thread_id}")
                if channel is not None:
                    await channel.publish("upload_complete", {
                        "status": "upload_complete",
                        "message": f"All {total_files} files uploaded",
                        "file_count": total_files,
                        "files": [{"path": p, "size": len(c)} for p, c in file_system.items()],
                        "refresh_required": True,
                        "organization_slug": org_slug,
                        "project_slug": project_slug,
                    })
                    logger.info("persistence_node: Published upload_complete")
            except Exception as e:
                logger.error(
                    f"persistence_node: Failed to publish upload_complete: {e}")

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
            "files": files_payload,
        }

        headers = {
            "Authorization": f"Bearer {webhook_secret}",
            "Content-Type": "application/json",
        }

        logger.info(f"persistence_node: POSTing to {endpoint} for {org_slug}/{project_slug}")

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
                        f"Uploaded {uploaded}/{total} files to Azure")

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
                    error_msg = (
                        f"Backend upload failed with status {response.status}: {text[:300]}, "
                        f"endpoint={endpoint}"
                    )
                    logger.error(f"persistence_node: {error_msg}")
                    build_logs.append(f"Error: {error_msg}")
                    return {"build_ready": False, "build_logs": build_logs}

    except ImportError:
        logger.error("persistence_node: aiohttp package not installed")
        build_logs.append(
            "Error: aiohttp not installed. Run: pip install aiohttp")
        return {"build_ready": False, "build_logs": build_logs}

    except Exception as e:
        error_msg = f"Backend upload failed: {type(e).__name__}: {e!r} (BACKEND_URL={backend_url})"
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
