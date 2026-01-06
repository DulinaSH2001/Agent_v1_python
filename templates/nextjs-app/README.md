# Next.js Template

This is a pre-configured Next.js 16 template with Shadcn UI components.

## Features

- ✅ Next.js 16 with App Router
- ✅ TypeScript (strict mode)
- ✅ Tailwind CSS
- ✅ Shadcn UI Components
- ✅ Dark mode support
- ✅ Server Actions template
- ✅ Type definitions

## Available Components

| Component | Path | Features |
|-----------|------|----------|
| Button | `components/ui/button.tsx` | 6 variants, 4 sizes |
| Card | `components/ui/card.tsx` | Header, content, footer |
| Input | `components/ui/input.tsx` | Full form support |
| Label | `components/ui/label.tsx` | Radix UI based |
| Table | `components/ui/table.tsx` | Full table structure |
| Dialog | `components/ui/dialog.tsx` | Modal dialogs |
| Badge | `components/ui/badge.tsx` | Status indicators |
| Skeleton | `components/ui/skeleton.tsx` | Loading states |

## Quick Start

```bash
# Install dependencies
npm install

# Run development server
npm run dev
```

## Usage with Antigravity Agent

This template is automatically used by the Antigravity agent as a base.
The agent will generate additional files on top of this structure.

## Structure

```
templates/nextjs-app/
├── app/
│   ├── layout.tsx      # Root layout
│   └── page.tsx        # Home page
├── components/
│   ├── ui/             # Shadcn components
│   └── theme-provider.tsx
├── lib/
│   ├── utils.ts        # Utility functions
│   └── actions.ts      # Server actions
├── styles/
│   └── globals.css     # Global styles
├── types/
│   └── index.ts        # Type definitions
├── package.json
├── next.config.js
├── tailwind.config.js
└── tsconfig.json
```
