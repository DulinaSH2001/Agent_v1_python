"""
Test script for automated build and fix system.

This demonstrates the full auto-fix loop:
1. Generate project
2. Automatically run npm install && npm run dev
3. Detect errors
4. Auto-fix with reflexion loop
5. Rebuild until success
"""

import asyncio
import logging
from agent.auto_build_runner import auto_build_and_fix

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def test_auto_build():
    """Test automated build with a simple Next.js project."""

    # Minimal test project with intentional error
    file_system = {
        "package.json": """{
  "name": "test-auto-build",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start"
  },
  "dependencies": {
    "next": "^15.1.4",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@types/node": "^20",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "typescript": "^5"
  }
}""",
        "tsconfig.json": """{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [
      {
        "name": "next"
      }
    ],
    "paths": {
      "@/*": ["./*"]
    }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}""",
        "next.config.mjs": """/** @type {import('next').NextConfig} */
const nextConfig = {
    reactStrictMode: true,
};

export default nextConfig;
""",
        "app/layout.tsx": """export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
""",
        "app/page.tsx": """export default function Home() {
  return (
    <div>
      <h1>Hello Auto-Build!</h1>
      <p>This project should compile successfully.</p>
    </div>
  )
}
""",
    }

    logger.info("Starting automated build test...")
    logger.info(f"Files to build: {list(file_system.keys())}")

    # Run build
    status, error_logs = await auto_build_and_fix(file_system, "test-auto-build")

    logger.info(f"\n{'='*60}")
    logger.info(f"BUILD RESULT: {status}")
    logger.info(f"{'='*60}")

    if status == "success":
        logger.info("✅ Build succeeded!")
    else:
        logger.error("❌ Build failed with errors:")
        for error in error_logs:
            logger.error(f"  - {error}")

    return status == "success"


if __name__ == "__main__":
    success = asyncio.run(test_auto_build())
    exit(0 if success else 1)
