#!/usr/bin/env python3
"""
Quick Redis Connection Test
Verify that the REDIS_URL is properly configured and accessible.
"""

import os
import sys
from dotenv import load_dotenv
from pathlib import Path

# Load .env
env_file = Path(".env")
if not env_file.exists():
    print("❌ .env file not found in", os.getcwd())
    sys.exit(1)

load_dotenv(env_file)

print("="*60)
print("  Redis Connection Test")
print("="*60)

# Check REDIS_URL
redis_url = os.getenv("REDIS_URL", "").strip()
if not redis_url:
    print("\n❌ ERROR: REDIS_URL not configured in .env")
    print("\n📝 To fix, add to your .env file:")
    print("   REDIS_URL=redis://user:password@host:port")
    sys.exit(1)

print(f"\n✅ REDIS_URL found")
print(f"   Format: {redis_url[:40]}... (masked for security)")

# Try to connect
try:
    import redis
    print("\n🔄 Attempting Redis connection...")

    client = redis.from_url(redis_url, decode_responses=True)

    # Test ping
    pong = client.ping()
    if pong:
        print("✅ Redis connection successful - PING returned", pong)

    # Test set/get
    client.set("test_key", "test_value")
    value = client.get("test_key")
    if value == "test_value":
        print("✅ Redis read/write test passed")
    client.delete("test_key")

    print("\n" + "="*60)
    print("  ✅ Redis is Ready!")
    print("="*60)
    print("\nYou can now run:")
    print("  python3 test_full_workflow.py")

except ImportError:
    print("❌ redis-py not installed")
    print("   Install with: pip install redis")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ Redis connection failed: {e}")
    print("\nTroubleshooting:")
    print("  1. Verify REDIS_URL format: redis://user:password@host:port")
    print("  2. Check if your Upstash Redis instance is active")
    print("  3. Try: telnet <host> 6379")
    sys.exit(1)
