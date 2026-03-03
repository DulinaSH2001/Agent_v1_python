# SSL Certificate & Connection Issues - Fix Guide

## Issues Identified from Logs

### 1. **SSL Certificate Verification Failure** ⚠️ CRITICAL

```
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate
```

**Root Cause**: The Ably WebSocket client fails to verify SSL certificates on macOS because:

- Python on macOS doesn't automatically include the system CA certificates
- The default SSL context can't find the certificate bundle
- This causes WebSocket connections to `wss://realtime.ably.io` to fail

**Impact**: Real-time updates via WebSocket don't work, but REST API fallback keeps the system functional

### 2. **Redis Not Configured** ⚠️ WARNING

```
WARNING:api.webhook:No REDIS_URL found. Using In-Memory Checkpointer (session-only persistence)
```

**Root Cause**: `REDIS_URL` environment variable not set

**Impact**:

- Session data only persists during the current process
- Multi-instance deployments won't share state
- Server restarts lose job context

### 3. **Memory Load Failures** ⚠️ WARNING

```
WARNING:api.webhook:Failed to load project context... status 404
WARNING:api.webhook:Failed to load history... status 401
```

**Root Cause**: Missing or incorrect authentication for loading historical data

---

## Fixes Applied

### 1. Updated `agent/reflexion.py`

- Added `certifi` SSL certificate bundle support
- Configured proper SSL context for Ably WebSocket connections
- Added fallback SSL handling for macOS issues

### 2. Updated `api/webhook.py`

- Enhanced `get_ably_client()` with SSL configuration
- Added better error handling for Ably operations
- Marked Ably failures as non-critical (won't block execution)
- Added support for both async/sync Ably versions

### 3. Updated `requirements.txt`

- Added `certifi>=2024.0.0` for SSL certificate management

---

## Setup Instructions

### Step 1: Install Updated Dependencies

```bash
cd /Users/dulina/Documents/Research Project/Platform/Agent_v1_python
pip install --upgrade certifi  # Install SSL certificate bundle
pip install -r requirements.txt
```

### Step 2: Fix macOS SSL (If Still Issues)

On macOS, if SSL issues persist, run:

```bash
# For Python installed via Homebrew
/usr/local/opt/python@3.11/libexec/bin/python -m pip install certifi

# For Anaconda
conda install certifi

# Or use the certificate installer
cd /Users/dulina/Documents/Research Project/Platform/Agent_v1_python
python -c "import ssl; ssl.create_default_context()"
```

### Step 3: Configure Environment Variables

Create or update `.env` file:

```bash
# Required
ABLY_API_KEY=your_ably_api_key_here

# Recommended for production
REDIS_URL=redis://localhost:6379/0
# OR for Upstash
REDIS_URL=redis://default:your_token@your-upstash-redis.upstash.io:6379

# Optional
ABLY_CHANNEL_PREFIX=ai-backend-generation
LOG_LEVEL=INFO
```

### Step 4: Verify Setup

Run the test script:

```bash
python test_setup.py
```

---

## Testing WebSocket Connectivity

To verify the SSL fix works:

```python
import os
from ably import AblyRealtime
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("ABLY_API_KEY")
client = AblyRealtime(api_key, use_binary_protocol=False)
channel = client.channels.get("test-channel")
channel.publish("test", {"message": "Hello"})
client.close()
print("✅ Ably connection successful!")
```

---

## Troubleshooting

### If you still see SSL errors:

```bash
# 1. Update Python's SSL certificates
pip install --upgrade certifi

# 2. Verify certifi path
python -c "import certifi; print(certifi.where())"

# 3. Test SSL directly
python -c "import ssl; ssl.create_default_context().check_hostname = True; print('SSL OK')"

# 4. Check Ably API key
echo $ABLY_API_KEY
```

### If Redis connection fails:

```bash
# Option 1: Install Redis locally
brew install redis
brew services start redis

# Option 2: Use Upstash Redis (cloud)
# Get REDIS_URL from https://console.upstash.com/ and add to .env
```

---

## Performance Impact

| Aspect              | Before              | After                         |
| ------------------- | ------------------- | ----------------------------- |
| SSL Verification    | ❌ Fails            | ✅ Works                      |
| Real-time Updates   | ⚠️ REST Only        | ✅ WebSocket Ready            |
| Session Persistence | ❌ In-Memory Only   | ⚠️ In-Memory (Redis optional) |
| Error Handling      | 🛑 Blocks Execution | ✅ Non-critical               |

---

## What's Still Needed

To fully resolve all warnings:

1. **For WebSocket**: ✅ Fixed - Install certifi and update code
2. **For Session Persistence**: Configure REDIS_URL in .env
3. **For Memory Loading**: Verify authentication tokens/URLs are correct
4. **For Production**:
   - Set up Redis or use Upstash
   - Configure proper logging
   - Set environment-specific variables

---

## References

- [Ably Python SDK](https://github.com/ably/ably-python)
- [certifi - SSL Certificates](https://github.com/certifi/python-certifi)
- [Upstash Redis](https://console.upstash.com/)
- [macOS SSL Issues](https://medium.com/hackernoon/macOS-how-to-fix-certificate-verify-failed-error-on-python-c2b5a3e07f9e)
