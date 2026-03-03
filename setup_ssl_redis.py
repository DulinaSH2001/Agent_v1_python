#!/usr/bin/env python3
"""
Quick setup script to fix SSL and Redis configuration issues.
Run this after cloning the repo to ensure all dependencies are properly installed.
"""

import os
import sys
import subprocess
import platform
from pathlib import Path


def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def run_command(cmd, description):
    """Run a command and return success status."""
    print(f"▶ {description}...")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  ✅ Success")
            return True
        else:
            print(f"  ⚠️  {result.stderr[:100]}")
            return False
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def main():
    print_section("Agent SSL & Redis Configuration Setup")

    # Get the script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)

    print(f"Working directory: {os.getcwd()}")
    print(f"Python version: {sys.version}")
    print(f"Platform: {platform.system()}")

    # 1. Install/Upgrade certifi
    print_section("1. Installing SSL Certificate Bundle (certifi)")
    run_command("pip install --upgrade certifi", "Upgrading certifi")

    # 2. Install requirements
    print_section("2. Installing Project Dependencies")
    run_command("pip install -r requirements.txt",
                "Installing requirements.txt")

    # 3. Verify SSL setup
    print_section("3. Verifying SSL Setup")
    try:
        import certifi
        print(f"✅ certifi installed at: {certifi.where()}")
    except ImportError:
        print("❌ certifi not found - SSL verification may fail")

    try:
        import ssl
        ctx = ssl.create_default_context()
        print(f"✅ SSL context created successfully")
    except Exception as e:
        print(f"⚠️  SSL context issue: {e}")

    # 4. Check for environment file
    print_section("4. Checking Environment Configuration")

    env_file = Path(".env")
    if env_file.exists():
        print("✅ .env file found")
    else:
        print("\n⚠️  .env file not found. Creating template...\n")

        env_template = """# Agent Configuration
# ======================================

# Required: Ably API Key (get from https://ably.com/)
ABLY_API_KEY=your_ably_api_key_here

# Optional: Ably Channel Prefix
ABLY_CHANNEL_PREFIX=ai-backend-generation

# Optional: Redis URL for persistent session storage
# For local Redis:
# REDIS_URL=redis://localhost:6379/0

# For Upstash Redis (cloud):
# REDIS_URL=redis://default:your_token@your-upstash-redis.upstash.io:6379

# Logging
LOG_LEVEL=INFO

# Azure Configuration (if using Azure OpenAI)
# AZURE_OPENAI_API_KEY=your_key_here
# AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
"""
        with open(".env", "w") as f:
            f.write(env_template)
        print("📄 Created .env template - Please configure your API keys")

    # 5. Verify Ably setup
    print_section("5. Testing Ably Connection")

    ably_test_code = '''
import os
try:
    from ably import AblyRest
    api_key = os.getenv("ABLY_API_KEY")
    if api_key and api_key != "your_ably_api_key_here":
        client = AblyRest(api_key, use_binary_protocol=False)
        print("✅ Ably REST client initialized successfully")
        client.close()
    else:
        print("⚠️  ABLY_API_KEY not configured in .env")
except Exception as e:
    print(f"⚠️  Ably test failed: {e}")
'''

    subprocess.run([sys.executable, "-c", ably_test_code],
                   env={**os.environ, "PYTHONPATH": str(script_dir)})

    # 6. Summary
    print_section("Setup Summary")

    print("✅ Installation complete!\n")
    print("Next steps:")
    print("  1. Configure your .env file with API keys")
    print("  2. Run: python test_setup.py")
    print("  3. Start the webhook server: uvicorn api.webhook:app --reload")
    print("\nFor troubleshooting, see: SSL_CERTIFICATE_FIX.md")


if __name__ == "__main__":
    main()
