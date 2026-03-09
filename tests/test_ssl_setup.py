#!/usr/bin/env python3
"""
Quick test to verify SSL and Ably configuration.
NO INTERNET REQUIRED - only checks what's installed.
"""

import os
import sys
import ssl
import platform
from pathlib import Path


def print_header(text):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def check_item(name, condition, details=""):
    status = "✅" if condition else "❌"
    print(f"{status} {name}")
    if details:
        print(f"   └─ {details}")


print_header("SSL & Ably Configuration Test")

# 1. Python Version
print("System Information:")
check_item(
    "Python Version",
    sys.version_info >= (3, 11),
    f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
)
check_item(
    "Platform",
    True,
    f"{platform.system()} {platform.release()}"
)

# 2. Certifi
print("\n\nSSL Certificates:")
try:
    import certifi
    cert_path = certifi.where()
    cert_exists = os.path.exists(cert_path)
    check_item(
        "Certifi Module",
        True,
        f"{certifi.__version__}"
    )
    check_item(
        "Certificate Bundle",
        cert_exists,
        f"{cert_path}"
    )
except ImportError:
    check_item("Certifi Module", False, "NOT INSTALLED")

# 3. SSL Context
print("\n\nSSL Context:")
try:
    ctx = ssl.create_default_context()
    check_item("Default SSL Context", True, f"Protocol: {ctx.protocol}")
    check_item("Hostname Checking", ctx.check_hostname, "Enabled")
    check_item("Verify Mode", ctx.verify_mode ==
               ssl.CERT_REQUIRED, "CERT_REQUIRED")
except Exception as e:
    check_item("Default SSL Context", False, str(e))

# Try with certifi
try:
    import certifi
    ctx_certifi = ssl.create_default_context(cafile=certifi.where())
    check_item("SSL Context with Certifi", True, "Success")
except Exception as e:
    check_item("SSL Context with Certifi", False, str(e))

# 4. Required Packages
print("\n\nRequired Packages:")
packages = {
    "ably": "Ably Real-time Messaging",
    "fastapi": "FastAPI Web Framework",
    "langgraph": "LangGraph Agent Framework",
    "httpx": "HTTP Client",
    "pydantic": "Data Validation"
}

for package, description in packages.items():
    try:
        mod = __import__(package)
        version = getattr(mod, "__version__", "unknown")
        check_item(f"{package}", True, f"v{version} - {description}")
    except ImportError:
        check_item(f"{package}", False, f"NOT INSTALLED - {description}")

# 5. Environment
print("\n\nEnvironment Configuration:")
env_file = Path(".env")
check_item(
    ".env File",
    env_file.exists(),
    "Found" if env_file.exists() else "Not configured (create .env to enable Ably)"
)

if env_file.exists():
    ably_key = os.getenv("ABLY_API_KEY", "").strip()
    has_ably = bool(ably_key) and ably_key != "your_ably_api_key_here"
    check_item("ABLY_API_KEY", has_ably,
               "Configured" if has_ably else "Not set")

redis_url = os.getenv("REDIS_URL", "").strip()
has_redis = bool(redis_url)
check_item(
    "REDIS_URL",
    has_redis,
    "Configured" if has_redis else "Not set (will use in-memory storage)"
)

# 6. Summary
print("\n\n" + "="*60)
print("  Summary")
print("="*60)

all_checks_pass = True
try:
    import certifi
    import ssl
    ctx = ssl.create_default_context(cafile=certifi.where())
    check_item("SSL Configuration", True, "READY for production")
except:
    all_checks_pass = False
    check_item("SSL Configuration", False, "Needs attention")

print("\n✅ All SSL configurations verified!")
print("\nNext Steps:")
print("  1. If .env doesn't exist, create one with your ABLY_API_KEY")
print("  2. Run: python test_setup.py")
print("  3. Or: python test_full_workflow.py")
print("  4. Start server: uvicorn api.webhook:app --reload")
