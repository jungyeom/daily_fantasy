"""Auto-debug system that invokes Claude Code to fix errors automatically.

When a job fails, this module:
1. Captures full error context (exception, stack trace, job state, recent logs)
2. Writes error details to a structured file
3. Invokes Claude Code CLI to analyze and fix the issue
4. Claude creates a feature branch, applies fixes, and can merge if tests pass
"""
import json
import logging
import os
import subprocess
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Directory for error context files
ERROR_DIR = Path(__file__).parent.parent.parent / "data" / "errors"


class AutoDebugger:
    """Handles automatic error debugging via Claude Code."""

    def __init__(self, enabled: bool = True):
        """Initialize auto-debugger.

        Args:
            enabled: Whether auto-debugging is enabled
        """
        self.enabled = enabled
        self.error_dir = ERROR_DIR
        self.error_dir.mkdir(parents=True, exist_ok=True)

    def handle_error(
        self,
        error: Exception,
        job_name: str,
        job_args: Optional[dict] = None,
        additional_context: Optional[dict] = None,
    ) -> Optional[str]:
        """Handle an error by invoking Claude to debug and fix it.

        Args:
            error: The exception that occurred
            job_name: Name of the job that failed
            job_args: Arguments passed to the job
            additional_context: Any additional context (e.g., recent logs)

        Returns:
            Path to the error context file, or None if disabled
        """
        if not self.enabled:
            logger.debug("Auto-debug disabled, skipping")
            return None

        # Capture error context
        error_context = self._build_error_context(
            error, job_name, job_args, additional_context
        )

        # Write to file
        error_file = self._write_error_file(error_context)

        # Invoke Claude Code
        self._invoke_claude(error_file, error_context)

        return str(error_file)

    def _build_error_context(
        self,
        error: Exception,
        job_name: str,
        job_args: Optional[dict] = None,
        additional_context: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Build comprehensive error context.

        Args:
            error: The exception
            job_name: Job name
            job_args: Job arguments
            additional_context: Additional context

        Returns:
            Error context dictionary
        """
        return {
            "timestamp": datetime.now().isoformat(),
            "job_name": job_name,
            "job_args": job_args or {},
            "error_type": type(error).__name__,
            "error_message": str(error),
            "stack_trace": traceback.format_exc(),
            "additional_context": additional_context or {},
            "working_directory": str(Path.cwd()),
            "python_path": os.environ.get("PYTHONPATH", ""),
        }

    def _write_error_file(self, error_context: dict) -> Path:
        """Write error context to a JSON file.

        Args:
            error_context: Error context dictionary

        Returns:
            Path to the error file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_name = error_context["job_name"].replace(" ", "_")
        filename = f"error_{job_name}_{timestamp}.json"
        error_file = self.error_dir / filename

        with open(error_file, "w") as f:
            json.dump(error_context, f, indent=2, default=str)

        logger.info(f"Error context written to {error_file}")
        return error_file

    def _invoke_claude(self, error_file: Path, error_context: dict) -> None:
        """Invoke Claude Code CLI to debug the error.

        Args:
            error_file: Path to error context file
            error_context: Error context dictionary
        """
        # Build the prompt for Claude
        prompt = self._build_debug_prompt(error_context)

        logger.info(f"Invoking Claude Code to debug: {error_context['job_name']}")

        try:
            # Run Claude Code in a subprocess
            # Using --dangerously-skip-permissions to allow autonomous operation
            # This requires user to have accepted this in their Claude Code settings
            cmd = [
                "claude",
                "--dangerously-skip-permissions",
                "-p", prompt,
            ]

            # Run in background so scheduler can continue
            subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(Path(__file__).parent.parent.parent),  # Project root
                start_new_session=True,  # Detach from parent process
            )

            logger.info("Claude Code invoked successfully (running in background)")

        except FileNotFoundError:
            logger.error("Claude Code CLI not found. Install with: npm install -g @anthropic-ai/claude-code")
        except Exception as e:
            logger.error(f"Failed to invoke Claude Code: {e}")

    def _build_debug_prompt(self, error_context: dict) -> str:
        """Build the prompt for Claude to debug the error.

        Args:
            error_context: Error context dictionary

        Returns:
            Prompt string
        """
        return f"""AUTOMATED ERROR DEBUG REQUEST

A scheduled job has failed and needs debugging. Please:

1. ANALYZE the error below
2. CREATE a feature branch named 'fix/{error_context['job_name'].lower().replace(' ', '-')}-{datetime.now().strftime('%Y%m%d%H%M')}'
3. IDENTIFY the root cause by reading relevant source files
4. IMPLEMENT a fix
5. TEST the fix if possible
6. COMMIT changes with a clear message
7. SEND an email notification about what was fixed (use the notify_error function with success context)

If you cannot fix the issue automatically, document what you found and send a detailed error email.

ERROR DETAILS:
==============
Job: {error_context['job_name']}
Time: {error_context['timestamp']}
Error Type: {error_context['error_type']}
Error Message: {error_context['error_message']}

Job Arguments:
{json.dumps(error_context['job_args'], indent=2)}

Stack Trace:
{error_context['stack_trace']}

Additional Context:
{json.dumps(error_context['additional_context'], indent=2, default=str)}

IMPORTANT GUIDELINES:
- Create a feature branch, do NOT commit directly to main
- After fixing, you may merge to main if tests pass
- If the error is in configuration/data (not code), document it but don't try to fix code
- Send email notification via SendGrid using the existing notifier
- Be thorough but efficient - the scheduler is waiting
"""


# Singleton instance
_auto_debugger: Optional[AutoDebugger] = None


def get_auto_debugger() -> AutoDebugger:
    """Get the auto-debugger singleton."""
    global _auto_debugger
    if _auto_debugger is None:
        _auto_debugger = AutoDebugger()
    return _auto_debugger


def handle_job_error(
    error: Exception,
    job_name: str,
    job_args: Optional[dict] = None,
    additional_context: Optional[dict] = None,
) -> None:
    """Convenience function to handle a job error.

    Args:
        error: The exception that occurred
        job_name: Name of the job that failed
        job_args: Arguments passed to the job
        additional_context: Any additional context
    """
    debugger = get_auto_debugger()
    debugger.handle_error(error, job_name, job_args, additional_context)
