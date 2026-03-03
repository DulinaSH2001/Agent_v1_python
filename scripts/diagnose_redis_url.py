#!/usr/bin/env python3
"""
Configure Redis URL from Upstash REST credentials.
The native Redis protocol (6379) may not work, so we'll use the REST API.
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv

# Load current .env
env_file = Path(".env")
load_dotenv(env_file)

rest_url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
rest_token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()

if not rest_url or not rest_token:
    print("❌ UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN not configured")
    exit(1)

print("Upstash REST Configuration:")
print(f"  REST URL: {rest_url}")
print(f"  REST Token: {rest_token[:20]}...")

# Parse the REST URL to extract the hostname for native protocol
# Example: https://relative-reindeer-43572.upstash.io
# Extract just the hostname
match = re.search(r'https?://([^/]+)', rest_url)
if match:
    hostname = match.group(1)
    print(f"  Extracted hostname: {hostname}")

    # Try to resolve it via DNS-over-HTTPS (more reliable)
    import socket
    try:
        ip = socket.gethostbyname(hostname)
        print(f"  ✅ Resolved to IP: {ip}")
    except socket.gaierror:
        print(f"  ❌ Cannot resolve {hostname} via standard DNS")
        print("\n💡 Upstash may require native Redis protocol on a different port.")
        print("   Using REST API instead for reliability...\n")

# Generate the REDIS_URL using the REST API format
# Upstash REST API format for LangGraph checkpoint
# https://docs.upstash.com/redis/features/restapi
redis_url = f"upstash://{rest_token}@{rest_url.replace('https://', '')}"

print(f"\nSuggested REDIS_URL (using REST API):")
print(f"  {redis_url}\n")

# Alternatively, try https:// protocol
redis_url_https = f"https://{rest_token}@{rest_url.replace('https://', '')}"
print(f"Alternative (HTTPS):")
print(f"  {redis_url_https}\n")

# Check if langgraph supports upstash protocol
print("Checking LangGraph checkpoint implementations...")
try:
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    print("✅ AsyncRedisSaver available")
except ImportError:
    print("❌ AsyncRedisSaver not found")

try:
    from upstash_redis import Redis as UpstashRedis
    print("✅ Upstash Redis SDK available")
except ImportError:
    print("❌ Upstash Redis SDK not installed (pip install upstash-redis)")
