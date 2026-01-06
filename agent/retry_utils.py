"""
Antigravity Agent - Retry Utilities

Provides retry decorators for handling transient failures in async operations.
"""

import asyncio
import logging
from functools import wraps
from typing import TypeVar, Callable, Tuple, Type

T = TypeVar('T')
logger = logging.getLogger(__name__)


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Retry decorator for async functions with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exception types to retry on
        
    Example:
        @async_retry(max_attempts=3, delay=1.0, backoff=2.0)
        async def upload_file(path, content):
            await blob_client.upload_blob(content)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay
            last_exception = None
            
            while attempt < max_attempts:
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    attempt += 1
                    
                    if attempt >= max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise
                    
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {current_delay:.1f}s..."
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            
            # Should never reach here, but just in case
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator


def format_error_context(error: Exception, **context) -> str:
    """
    Format an error message with helpful context information.
    
    Args:
        error: The exception that occurred
        **context: Keyword arguments providing context (file_path, operation, etc.)
        
    Returns:
        Formatted error message with context
        
    Example:
        msg = format_error_context(
            e,
            operation="upload",
            file_path="app/page.tsx",
            container="project-123"
        )
    """
    msg = f"❌ {type(error).__name__}: {str(error)}\n"
    
    if context:
        msg += "\n📋 Context:\n"
        for key, value in context.items():
            # Format key nicely (snake_case -> Title Case)
            formatted_key = key.replace('_', ' ').title()
            msg += f"  • {formatted_key}: {value}\n"
    
    return msg
