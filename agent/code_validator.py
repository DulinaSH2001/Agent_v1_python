"""
Antigravity Agent - Code Validator

Provides validation for generated code before upload to catch syntax errors early.
"""

import ast
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def validate_python(code: str) -> Tuple[bool, str]:
    """
    Validate Python code syntax using AST parsing.
    
    Args:
        code: Python source code to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Parse error: {str(e)}"


def validate_typescript(code: str) -> Tuple[bool, str]:
    """
    Basic TypeScript/JavaScript validation (checks for common syntax errors).
    
    Note: This is a lightweight check, not a full parser.
    For production, consider using a proper TS/JS parser.
    
    Args:
        code: TypeScript/JavaScript source code to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check for balanced braces, brackets, parentheses
    checks = [
        ('{', '}', "Unmatched curly braces"),
        ('(', ')', "Unmatched parentheses"),
        ('[', ']', "Unmatched square brackets"),
    ]
    
    for open_char, close_char, error_msg in checks:
        open_count = code.count(open_char)
        close_count = code.count(close_char)
        if open_count != close_count:
            return False, f"{error_msg} (found {open_count} '{open_char}' and {close_count} '{close_char}')"
    
    # Check for common syntax errors
    if 'import {' in code and '} from' not in code:
        return False, "Incomplete import statement (missing '} from')"
    
    # Check for unterminated strings (simple check)
    single_quotes = code.count("'") - code.count("\\'")
    double_quotes = code.count('"') - code.count('\\"')
    backticks = code.count('`') - code.count('\\`')
    
    if single_quotes % 2 != 0:
        return False, "Unterminated single-quoted string"
    if double_quotes % 2 != 0:
        return False, "Unterminated double-quoted string"
    if backticks % 2 != 0:
        return False, "Unterminated template literal"
    
    return True, ""


def validate_json(code: str) -> Tuple[bool, str]:
    """
    Validate JSON syntax.
    
    Args:
        code: JSON content to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        import json
        json.loads(code)
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"JSON error at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Parse error: {str(e)}"


def validate_file(file_path: str, content: str) -> Tuple[bool, str]:
    """
    Validate file content based on its extension.
    
    Args:
        file_path: Path to the file (used to determine type)
        content: File content to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Python files
    if file_path.endswith('.py'):
        return validate_python(content)
    
    # TypeScript/JavaScript files
    elif file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
        return validate_typescript(content)
    
    # JSON files
    elif file_path.endswith('.json'):
        return validate_json(content)
    
    # Other files - skip validation
    else:
        return True, ""


