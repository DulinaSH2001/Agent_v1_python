"""
Antigravity Agent - Automated Build Runner

Automatically runs `npm install && npm run dev` after generation and
feeds build results back to the reflexion loop for auto-fixing.

This eliminates the need for manual build triggering and enables
fully autonomous error detection and correction.
"""

import asyncio
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AutoBuildRunner:
    """
    Automated build runner that executes npm commands and captures errors.

    Features:
    - Writes files to temporary directory
    - Runs npm install
    - Runs npm run dev with timeout
    - Parses error logs for structured feedback
    - Returns build status and error details
    """

    def __init__(
        self,
        install_timeout: int = 120,  # 2 minutes for npm install
        build_timeout: int = 60,     # 1 minute for initial compilation
    ):
        self.install_timeout = install_timeout
        self.build_timeout = build_timeout

    async def run_build(
        self,
        file_system: Dict[str, str],
        project_name: str = "generated-project",
    ) -> Tuple[str, List[str], List[str]]:
        """
        Run full build process and return results.

        Args:
            file_system: Dictionary of file paths to content
            project_name: Name for temporary directory

        Returns:
            Tuple of (status, stdout_logs, stderr_logs)
            status: "success", "failed", or "timeout"
        """
        logger.info(f"AutoBuildRunner: Starting build for {project_name}")

        # Create temporary directory for project
        temp_dir = tempfile.mkdtemp(prefix=f"{project_name}-")
        project_path = Path(temp_dir)

        try:
            # Step 1: Write all files to disk
            logger.info(f"Writing {len(file_system)} files to {project_path}")
            await self._write_files(project_path, file_system)

            # Step 2: Run npm install
            logger.info("Running npm install...")
            install_status, install_stdout, install_stderr = await self._run_npm_install(project_path)

            if install_status != "success":
                return install_status, install_stdout, install_stderr

            # Step 3: Run npm run dev (with timeout to catch initial compilation)
            logger.info("Running npm run dev...")
            build_status, build_stdout, build_stderr = await self._run_npm_dev(project_path)

            return build_status, build_stdout + install_stdout, build_stderr + install_stderr

        except Exception as e:
            logger.error(f"Build runner error: {e}")
            return "failed", [], [f"Build runner error: {str(e)}"]

        finally:
            # Cleanup temporary directory
            logger.info(f"Cleaning up temporary directory: {temp_dir}")
            await self._cleanup_directory(project_path)

    async def _write_files(self, project_path: Path, file_system: Dict[str, str]) -> None:
        """Write all files from file_system to disk."""
        for file_path, content in file_system.items():
            full_path = project_path / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logger.debug(f"Wrote file: {file_path}")

    async def _run_npm_install(self, project_path: Path) -> Tuple[str, List[str], List[str]]:
        """Run npm install and capture output."""
        try:
            process = await asyncio.create_subprocess_exec(
                'npm', 'install',
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.install_timeout
            )

            stdout_lines = stdout.decode('utf-8').split('\n') if stdout else []
            stderr_lines = stderr.decode('utf-8').split('\n') if stderr else []

            if process.returncode == 0:
                logger.info("✅ npm install succeeded")
                return "success", stdout_lines, stderr_lines
            else:
                logger.error(
                    f"❌ npm install failed with code {process.returncode}")
                return "failed", stdout_lines, stderr_lines

        except asyncio.TimeoutError:
            logger.error(
                f"❌ npm install timeout after {self.install_timeout}s")
            return "timeout", [f"npm install timed out after {self.install_timeout}s"], []
        except Exception as e:
            logger.error(f"❌ npm install error: {e}")
            return "failed", [], [f"npm install error: {str(e)}"]

    async def _run_npm_dev(self, project_path: Path) -> Tuple[str, List[str], List[str]]:
        """
        Run npm run dev and capture initial compilation output.

        We run dev server for a short time to catch compilation errors,
        then terminate it. This is enough to detect syntax/type errors.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                'npm', 'run', 'dev',
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Read output for a limited time to catch compilation
            stdout_lines = []
            stderr_lines = []

            try:
                # Wait for initial compilation (usually happens in first 10-60 seconds)
                await asyncio.sleep(self.build_timeout)

                # Check if process is still running (good sign)
                if process.returncode is None:
                    # Server is running, capture what we have so far
                    try:
                        stdout, stderr = await asyncio.wait_for(
                            process.communicate(),
                            timeout=2
                        )
                    except asyncio.TimeoutError:
                        # Process still running, manually read output
                        process.terminate()
                        await asyncio.sleep(1)
                        stdout, stderr = process.stdout, process.stderr

                        if stdout:
                            stdout_data = await stdout.read()
                            stdout_lines = stdout_data.decode(
                                'utf-8', errors='ignore').split('\n')
                        if stderr:
                            stderr_data = await stderr.read()
                            stderr_lines = stderr_data.decode(
                                'utf-8', errors='ignore').split('\n')

                    # Check output for compilation errors
                    all_output = '\n'.join(stdout_lines + stderr_lines)

                    if self._has_compilation_errors(all_output):
                        logger.error("❌ Compilation errors detected")
                        return "failed", stdout_lines, stderr_lines
                    elif "✓ Ready" in all_output or "compiled successfully" in all_output.lower():
                        logger.info("✅ Build succeeded - dev server running")
                        return "success", stdout_lines, stderr_lines
                    else:
                        logger.warning(
                            "⚠️ Dev server running but unclear status")
                        return "success", stdout_lines, stderr_lines

                else:
                    # Process exited (likely error)
                    stdout, stderr = await process.communicate()
                    stdout_lines = stdout.decode(
                        'utf-8').split('\n') if stdout else []
                    stderr_lines = stderr.decode(
                        'utf-8').split('\n') if stderr else []

                    logger.error(
                        f"❌ npm run dev exited with code {process.returncode}")
                    return "failed", stdout_lines, stderr_lines

            finally:
                # Always terminate the dev server
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()

        except Exception as e:
            logger.error(f"❌ npm run dev error: {e}")
            return "failed", [], [f"npm run dev error: {str(e)}"]

    def _has_compilation_errors(self, output: str) -> bool:
        """Check if output contains compilation errors."""
        error_patterns = [
            r'Error:',
            r'TypeError:',
            r'SyntaxError:',
            r'Module not found:',
            r'Cannot find module',
            r'✗ Failed to compile',
            r'Syntax error:',
            r'Type error:',
            r'⨯',  # Next.js error symbol
        ]

        for pattern in error_patterns:
            if re.search(pattern, output, re.IGNORECASE):
                return True

        return False

    async def _cleanup_directory(self, project_path: Path) -> None:
        """Clean up temporary directory."""
        try:
            import shutil
            await asyncio.sleep(0.5)  # Give processes time to release files
            shutil.rmtree(project_path, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Failed to cleanup directory: {e}")

    def parse_error_logs(self, stdout: List[str], stderr: List[str]) -> List[str]:
        """
        Parse error logs and extract meaningful error messages.

        Returns:
            List of structured error messages for the reflexion loop
        """
        errors = []
        all_lines = stdout + stderr

        # Common Next.js/TypeScript error patterns
        error_start_patterns = [
            r'Error:',
            r'⨯',
            r'✗',
            r'Failed to compile',
        ]

        in_error_block = False
        current_error = []

        for line in all_lines:
            line = line.strip()
            if not line:
                continue

            # Check if this line starts an error
            is_error_start = any(re.search(pattern, line)
                                 for pattern in error_start_patterns)

            if is_error_start:
                # Save previous error if exists
                if current_error:
                    errors.append('\n'.join(current_error))
                    current_error = []

                in_error_block = True
                current_error.append(line)
            elif in_error_block:
                # Continue collecting error lines
                # Stop at certain markers
                if line.startswith('at ') or 'node_modules' in line:
                    continue  # Skip stack traces
                elif re.match(r'^[\s├│└]+', line):
                    current_error.append(line)  # File tree lines
                else:
                    current_error.append(line)

        # Add last error
        if current_error:
            errors.append('\n'.join(current_error))

        return errors[:10]  # Limit to first 10 errors


# =============================================================================
# Integration with Reflexion Loop
# =============================================================================

async def auto_build_and_fix(
    file_system: Dict[str, str],
    project_name: str = "generated-project",
) -> Tuple[str, List[str]]:
    """
    Run build and return status + logs for reflexion loop.

    This function bridges the AutoBuildRunner with the agent's
    reflexion loop, providing structured error feedback.

    Args:
        file_system: Generated files
        project_name: Project name for logging

    Returns:
        Tuple of (status, error_logs)
    """
    runner = AutoBuildRunner()
    status, stdout, stderr = await runner.run_build(file_system, project_name)

    if status == "success":
        return "success", ["Build completed successfully"]

    # Parse errors for reflexion
    parsed_errors = runner.parse_error_logs(stdout, stderr)

    if not parsed_errors:
        # No specific errors found, return raw output
        return "failed", (stderr + stdout)[-20:]  # Last 20 lines

    return "failed", parsed_errors
