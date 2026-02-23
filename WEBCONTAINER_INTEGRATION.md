# WebContainer Auto-Build and Auto-Fix Integration

## Overview

This document describes the complete WebContainer integration that enables **automatic project building and auto-fixing** in the browser after AI code generation.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         WORKFLOW                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  1. AI Agent Generates Code → Python Agent                       │
│  2. Files Uploaded to Azure → Backend API                        │
│  3. Upload Complete Event → Ably                                 │
│  4. Frontend Receives Event → useGeneration Hook                 │
│  5. Files Auto-Mounted → WebContainer                            │
│  6. npm install Runs → Captures Output                           │
│  7. npm run dev Runs → Detects Errors                            │
│  8. Build Errors Detected → Send to Backend                      │
│  9. Backend Publishes to Ably → Python Agent                     │
│  10. Reflexion Loop Triggered → Auto-Fix                         │
│  11. Fixed Code Uploaded → Repeat 3-10 Until Success             │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Frontend - WebContainer Hook (`useWebContainer.ts`)

**Purpose**: Manage WebContainer lifecycle and build processes in the browser.

**Key Features**:

- Boot WebContainer with cross-origin isolation checks
- Mount file system from project files
- Run npm install with timeout protection
- Execute npm run dev and capture compilation errors
- Detect build failures via regex patterns
- Emit events for build errors

**API**:

```typescript
const { boot, mountFiles, install, runDev, stop, state } = useWebContainer({
  onReady: () => console.log("WebContainer ready"),
  onError: (error) => console.error(error),
  onServerReady: (port, url) => setPreviewUrl(url),
  onBuildError: (errors) => sendToBackend(errors),
  autoInstall: true,
  autoRun: true,
});
```

### 2. Frontend - Playground Page

**Purpose**: Coordinate WebContainer operations with generation lifecycle.

**Event Listeners**:

#### A. `webcontainer:mount-and-run`

Triggered when upload completes:

```typescript
window.addEventListener("webcontainer:mount-and-run", async (event) => {
  const { orgSlug, projectSlug, jobId } = event.detail;

  // Wait for files to refresh
  await new Promise((resolve) => setTimeout(resolve, 2000));

  // Run npm install
  const installProcess = await webContainer.spawn("npm", ["install"]);

  // Run npm run dev
  const devProcess = await webContainer.spawn("npm", ["run", "dev"]);

  // Monitor for errors
});
```

#### B. `webcontainer:build-error`

Triggered when build errors detected:

```typescript
window.addEventListener("webcontainer:build-error", async (event) => {
  const { phase, errors, fullOutput, jobId } = event.detail;

  // Send to backend API
  await fetch("/api/v1/generate/webhook/build-error", {
    method: "POST",
    body: JSON.stringify({
      job_id: jobId,
      phase,
      errors,
      fullOutput,
      orgSlug,
      projectSlug,
    }),
  });
});
```

### 3. Frontend - Generation Hook (`useGeneration.ts`)

**Purpose**: Manage generation state and Ably subscriptions.

**Upload Complete Handler**:

```typescript
channel.subscribe("upload_complete", (message) => {
  // Store job ID for error reporting
  localStorage.setItem(`webcontainer-job-${orgSlug}-${projectSlug}`, jobId);

  // Invalidate cache to refresh files
  dispatch(
    playgroundApi.util.invalidateTags([
      { type: "ProjectFiles", id: `${orgSlug}-${projectSlug}` },
    ])
  );

  // Trigger WebContainer auto-run
  window.dispatchEvent(
    new CustomEvent("webcontainer:mount-and-run", {
      detail: { orgSlug, projectSlug, jobId, fileCount },
    })
  );
});
```

### 4. Backend - Build Error Endpoint

**Purpose**: Receive WebContainer errors and forward to Python agent via Ably.

**Route**: `POST /api/v1/generate/webhook/build-error`

**Request Body**:

```json
{
  "job_id": "uuid-here",
  "phase": "install" | "dev",
  "errors": ["error message 1", "error message 2"],
  "fullOutput": "complete npm output...",
  "orgSlug": "my-org",
  "projectSlug": "my-project"
}
```

**Response**:

```json
{
  "success": true,
  "message": "Build error received and sent for auto-fixing",
  "job_id": "uuid-here"
}
```

**Implementation**:

```javascript
router.post("/webhook/build-error", async (req, res) => {
  const { job_id, phase, errors, fullOutput, orgSlug, projectSlug } = req.body;

  // Update job status
  await AIGenerationJobRepository.updateJob(job_id, {
    status: "fixing",
    progress: [
      {
        stage: "auto_fixing",
        percent: 80,
        message: "Auto-fixing build errors",
      },
    ],
  });

  // Publish to Ably for Python agent
  const channel = ablyClient.channels.get(`ai-backend-generation:${job_id}`);
  await channel.publish("build_error", {
    status: "build_error",
    phase,
    errors,
    fullOutput,
    orgSlug,
    projectSlug,
    timestamp: new Date().toISOString(),
  });

  res.json({ success: true, job_id });
});
```

### 5. Python Agent - WebContainer Integration

**Purpose**: Listen for WebContainer errors via Ably and trigger reflexion loop.

**Module**: `agent/webcontainer_integration.py`

**Key Function**:

```python
async def handle_webcontainer_error(
    job_id: str,
    build_error_data: Dict[str, Any],
    graph: Any,
    checkpointer: Any,
) -> Dict[str, Any]:
    """
    Handle WebContainer build errors and trigger reflexion.

    1. Retrieve current agent state from checkpointer
    2. Append error logs to build_logs
    3. Check iteration count (max 3)
    4. Invoke reflexion_node to analyze errors
    5. Generate fix tasks and append to implementation_plan
    6. Continue graph execution from generator node
    7. Auto-apply fixes and re-upload
    """
    # Get current state
    state = await checkpointer.aget(config)

    # Format errors
    error_logs = [f"WEBCONTAINER ERROR - {phase}", ...errors]

    # Update state
    updated_state = {
        **state,
        "build_status": "failed",
        "build_logs": [...state["build_logs"], ...error_logs]
    }

    # Trigger reflexion
    fixes = await reflexion_node(updated_state, config)

    # Continue execution
    result = await graph.ainvoke({**updated_state, **fixes}, config)

    return {"success": True, "fixes_generated": len(fixes)}
```

**Setup**:

```python
def setup_webcontainer_listener(ably_handler, graph, checkpointer):
    """Register build_error event handler with Ably."""
    async def on_build_error(message_data, job_id):
        await handle_webcontainer_error(job_id, message_data, graph, checkpointer)

    ably_handler.register_event_handler('build_error', on_build_error)
```

## Error Detection Patterns

### npm install Errors

```typescript
// Exit code check
if (exitCode !== 0) {
  // Send error with full output
  emit("build-error", { phase: "install", error: fullOutput });
}
```

### npm run dev Errors

```typescript
// Pattern matching
if (
  output.includes("Failed to compile") ||
  output.includes("Error:") ||
  output.includes("TypeError:") ||
  output.includes("SyntaxError:") ||
  output.includes("Module not found")
) {
  buildErrors.push(output);
  emit("build-error", { phase: "dev", errors: buildErrors, fullOutput });
}
```

## Auto-Fix Loop

### Iteration Control

- **Max Iterations**: 3 (configurable via `MAX_REFLEXION_ITERATIONS`)
- **Escalation**: After max iterations, escalate to human intervention
- **Status Tracking**: Each iteration updates job status in database

### Fix Generation Process

1. **Error Analysis** (Reflexion Node)

   - Parse error messages and stack traces
   - Identify root causes
   - Match to common patterns (TypeScript errors, missing modules, etc.)

2. **Fix Task Generation**

   - Create specific fix tasks with file paths
   - Provide code snippets and explanations
   - Append to `implementation_plan`

3. **Code Generation** (Generator Node)

   - Process fix tasks like regular tasks
   - Apply fixes to existing files
   - Maintain file context and structure

4. **Re-Upload** (Persistence Node)

   - Upload fixed files to Azure Blob Storage
   - Publish `upload_complete` event
   - Trigger WebContainer re-build

5. **Verification**
   - WebContainer mounts updated files
   - Re-runs npm install && npm run dev
   - Checks for remaining errors
   - If errors persist, repeat from step 1

## Configuration

### Environment Variables

**Frontend** (`.env.local`):

```env
NEXT_PUBLIC_API_URL=http://localhost:3002
NEXT_PUBLIC_ABLY_API_KEY=your-ably-key
```

**Backend** (`.env`):

```env
ABLY_API_KEY=your-ably-key
ABLY_CHANNEL_PREFIX=ai-backend-generation
```

**Python Agent** (`.env`):

```env
ABLY_API_KEY=your-ably-key
AZURE_OPENAI_ENDPOINT=https://your-endpoint.openai.azure.com
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
```

### Timeouts

- **npm install**: 120 seconds
- **npm run dev initial compilation**: 60 seconds
- **Error detection window**: 5 seconds after dev starts
- **File refresh wait**: 2 seconds after upload_complete

## Testing

### Manual Test Flow

1. **Start Generation**:

   ```typescript
   await startGeneration({
     query: "Create a Next.js login page",
     maxRevisions: 1,
     orgId,
     projectId,
     orgSlug,
     projectSlug,
   });
   ```

2. **Observe Auto-Run**:

   - Terminal shows "🚀 Auto-running project after upload..."
   - npm install output appears
   - npm run dev starts
   - Preview URL appears if successful

3. **Inject Error** (for testing auto-fix):
   - Modify generated code to have TypeScript error
   - Upload triggers re-build
   - Error detected and sent for auto-fix
   - Observe reflexion loop in terminal
   - Fixed code re-uploaded
   - Project builds successfully

### Integration Test

```python
# test_webcontainer_integration.py
async def test_webcontainer_error_handling():
    # Setup
    graph = create_antigravity_graph(checkpointer=checkpointer)
    setup_webcontainer_listener(ably_handler, graph, checkpointer)

    # Simulate WebContainer error
    error_data = {
        "phase": "dev",
        "errors": ["Type 'string' is not assignable to type 'number'"],
        "fullOutput": "Full TypeScript error...",
        "orgSlug": "test-org",
        "projectSlug": "test-project",
    }

    # Trigger handler
    result = await handle_webcontainer_error("job-123", error_data, graph, checkpointer)

    # Verify
    assert result["success"] == True
    assert result["fixes_generated"] > 0
```

## Monitoring

### Log Points

**Frontend**:

- `[Playground] 🔄 Auto-mount and run triggered`
- `[Playground] ✅ npm install completed`
- `[Playground] ⚠️ Build errors detected - sending for auto-fix`
- `[Playground] 📤 Sending build error to backend`

**Backend**:

- `📦 Received WebContainer build error`
- `📡 Publishing build error to Ably`
- `✅ Build error published for auto-fixing`

**Python Agent**:

- `[WebContainer] Received build error for job {job_id}`
- `[WebContainer] Triggering reflexion (iteration {n})`
- `[WebContainer] Generated {n} fix tasks`
- `[WebContainer] Auto-fix execution completed`

### Metrics to Track

- Time from error detection to fix applied
- Number of iterations per fix
- Fix success rate
- Common error patterns
- Escalation rate

## Troubleshooting

### WebContainer Not Booting

- **Check**: Cross-origin isolation headers (COOP/COEP)
- **Fix**: Ensure Next.js config has proper headers
- **Verify**: `window.crossOriginIsolated === true`

### Files Not Mounting

- **Check**: File paths and content encoding
- **Fix**: Verify base64 decoding in `decodeBase64ToUtf8`
- **Verify**: Console logs show "Mounted X files"

### Errors Not Detected

- **Check**: Regex patterns in error detection
- **Fix**: Add more error patterns if needed
- **Verify**: Console shows "Build errors detected"

### Auto-Fix Not Triggering

- **Check**: Ably connection and subscriptions
- **Fix**: Verify job ID in localStorage
- **Verify**: Backend receives POST to /webhook/build-error

### Infinite Loop

- **Check**: `MAX_REFLEXION_ITERATIONS` setting
- **Fix**: Increase timeout or improve error patterns
- **Verify**: Escalation triggers after max iterations

## Future Enhancements

1. **Parallel Error Resolution**: Fix multiple independent errors simultaneously
2. **Error Pattern Learning**: Cache successful fixes for faster resolution
3. **Visual Feedback**: Show fix progress in UI with detailed steps
4. **Manual Override**: Allow users to review/modify fixes before applying
5. **Performance Metrics**: Track build times and optimization suggestions

## Summary

This WebContainer integration provides a **fully autonomous** development workflow:

✅ **Auto-generate** code with AI  
✅ **Auto-upload** to cloud storage  
✅ **Auto-mount** to browser container  
✅ **Auto-build** with npm install && npm run dev  
✅ **Auto-detect** compilation errors  
✅ **Auto-fix** via reflexion loop  
✅ **Auto-iterate** until success (max 3 times)  
✅ **Auto-escalate** to human if needed

**Zero manual intervention required for most cases!**
