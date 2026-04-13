"""
Antigravity Agent - Conversation Memory

Provides persistent conversation context across generation sessions:
- ConversationMemory: Loads and manages conversation history from the backend
- Project context: Retrieves prior generation history for a project
- Context summarization: LLM-based summarization for long histories

This module enables the agent to remember previous generations, user
preferences, and make intelligent modifications based on history.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)


class ConversationMemory:
    """
    Manages persistent conversation context for the Antigravity agent.

    Fetches prior conversation history and project context from the
    Node.js backend, and provides LLM-based summarization for long
    histories to stay within context window limits.

    Attributes:
        backend_url: Backend API base URL.
        webhook_secret: Authentication token for internal API calls.
    """

    def __init__(
        self,
        backend_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ) -> None:
        self.backend_url = backend_url or os.getenv("BACKEND_URL", "http://localhost:8080")
        self.webhook_secret = webhook_secret or os.getenv("FASTAPI_WEBHOOK_SECRET", "")

    def _get_headers(self) -> Dict[str, str]:
        """Get authentication headers for backend requests."""
        return {
            "Authorization": f"Bearer {self.webhook_secret}",
            "Content-Type": "application/json",
        }

    async def load_history(self, job_id: str) -> List[Dict[str, Any]]:
        """
        Fetch prior conversation messages for a specific job.

        Args:
            job_id: The generation job ID to load history for.

        Returns:
            List of message dicts with role, content, timestamp, type.
        """
        try:
            import aiohttp

            endpoint = f"{self.backend_url}/api/v1/generate/agents/{job_id}/context"
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=self._get_headers(), timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        messages = data.get("data", {}).get("messages", [])
                        logger.info(f"Loaded {len(messages)} history messages for job {job_id}")
                        return messages
                    else:
                        logger.warning(f"Failed to load history for job {job_id}: status {response.status}")
                        return []

        except ImportError:
            logger.error("aiohttp not installed, cannot load history")
            return []
        except Exception as e:
            logger.error(
                f"Failed to load history for job {job_id}: {type(e).__name__}: {e!r} "
                f"(endpoint={self.backend_url}/api/v1/generate/agents/{job_id}/context)"
            )
            return []

    async def load_project_context(
        self,
        org_slug: str,
        project_slug: str,
    ) -> Dict[str, Any]:
        """
        Fetch previous generation history for a project.

        Returns prior jobs with their prompts, plans, and file lists,
        enabling the agent to understand what was previously generated.

        Args:
            org_slug: Organization slug.
            project_slug: Project slug.

        Returns:
            Dict with prior_generations list, each containing:
            - job_id, query, status, plan, file_paths, created_at
        """
        try:
            import aiohttp

            endpoint = (
                f"{self.backend_url}/api/v1/generate/projects/"
                f"{org_slug}/{project_slug}/generation-history"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=self._get_headers(), timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        context = data.get("data", {})
                        generations = context.get("generations", [])
                        logger.info(
                            f"Loaded project context for {org_slug}/{project_slug}: "
                            f"{len(generations)} prior generations"
                        )
                        return context
                    else:
                        logger.warning(
                            f"Failed to load project context for "
                            f"{org_slug}/{project_slug}: status {response.status}"
                        )
                        return {}

        except ImportError:
            logger.error("aiohttp not installed, cannot load project context")
            return {}
        except Exception as e:
            logger.error(
                f"Failed to load project context: {type(e).__name__}: {e!r} "
                f"(endpoint={self.backend_url}/api/v1/generate/projects/{org_slug}/{project_slug}/generation-history)"
            )
            return {}

    async def summarize_context(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 2000,
    ) -> str:
        """
        Summarize a long conversation history using a lightweight LLM.

        Uses GPT-4o-mini for cost-efficient summarization when the
        conversation history exceeds a reasonable length.

        Args:
            messages: List of conversation messages to summarize.
            max_tokens: Maximum tokens for the summary output.

        Returns:
            Summarized context string.
        """
        if not messages:
            return ""

        # If short enough, return as-is formatted
        formatted = self._format_messages(messages)
        if len(formatted) < 3000:
            return formatted

        # Use LLM for summarization
        try:
            from langchain_openai import ChatOpenAI, AzureChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage

            # Try Azure first, fall back to OpenAI
            azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
            azure_key = os.getenv("AZURE_OPENAI_API_KEY")

            if azure_endpoint and azure_key:
                llm = AzureChatOpenAI(
                    azure_endpoint=azure_endpoint,
                    api_key=azure_key,
                    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o"),
                    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
                    temperature=0.1,
                    max_tokens=max_tokens,
                )
            else:
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    logger.warning("No LLM configured for summarization, returning truncated context")
                    return formatted[:3000] + "\n... (truncated)"

                llm = ChatOpenAI(
                    model="gpt-4o-mini",
                    temperature=0.1,
                    max_tokens=max_tokens,
                    api_key=api_key,
                )

            response = await llm.ainvoke([
                SystemMessage(content=(
                    "You are a concise summarizer. Summarize the following conversation "
                    "between a user and an AI code generation agent. Focus on:\n"
                    "1. What the user requested\n"
                    "2. What was generated (key files and features)\n"
                    "3. Any feedback or modifications requested\n"
                    "4. User preferences (styling, patterns, libraries)\n"
                    "Keep the summary under 500 words."
                )),
                HumanMessage(content=formatted),
            ])

            summary = response.content.strip()
            logger.info(f"Summarized {len(messages)} messages into {len(summary)} chars")
            return summary

        except Exception as e:
            logger.error(f"Failed to summarize context: {e}")
            return formatted[:3000] + "\n... (truncated)"

    def _format_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Format messages into a readable string."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            msg_type = msg.get("message_type", "")
            if msg_type:
                lines.append(f"[{role} - {msg_type}]: {content}")
            else:
                lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    def build_context_prompt(
        self,
        project_context: Dict[str, Any],
        conversation_summary: str = "",
    ) -> str:
        """
        Build a context section to inject into the planning prompt.

        Args:
            project_context: Project generation history from load_project_context().
            conversation_summary: Summarized conversation from summarize_context().

        Returns:
            Formatted context string for the planning prompt.
        """
        sections = []

        # Prior generation summary
        generations = project_context.get("generations", [])
        if generations:
            sections.append("## Previous Generations")
            for i, gen in enumerate(generations[:5], 1):  # Last 5 generations
                query = gen.get("query", "Unknown request")
                status = gen.get("status", "unknown")
                file_count = len(gen.get("file_paths", []))
                created = gen.get("created_at", "")
                sections.append(
                    f"{i}. **{query[:100]}** — {status}, {file_count} files"
                    + (f" ({created[:10]})" if created else "")
                )

            # List files from the most recent generation
            if generations[0].get("file_paths"):
                sections.append("\n### Most Recent Generated Files")
                for fp in generations[0]["file_paths"][:20]:
                    sections.append(f"- {fp}")
                if len(generations[0]["file_paths"]) > 20:
                    sections.append(f"- ... and {len(generations[0]['file_paths']) - 20} more")

        # Conversation context
        if conversation_summary:
            sections.append("\n## Previous Conversation Context")
            sections.append(conversation_summary)

        if not sections:
            return ""

        return "\n".join(sections)


# Module-level convenience instance
_memory: Optional[ConversationMemory] = None


def get_memory() -> ConversationMemory:
    """Get or create the global ConversationMemory instance."""
    global _memory
    if _memory is None:
        _memory = ConversationMemory()
    return _memory
