# Pinecone Free Tier Setup Guide

This guide sets up Pinecone for the Antigravity Agent RAG system.
Uses the **free Serverless tier** — no credit card required.

---

## What You Get (Free Tier)

| Resource                   | Free Allowance         |
| -------------------------- | ---------------------- |
| Indexes                    | 1                      |
| Storage                    | 2 GB                   |
| Monthly queries            | Unlimited (Serverless) |
| Inference API (embeddings) | Free on all plans      |
| Dimension limit            | Up to 20,000           |
| Available region           | AWS `us-east-1` only   |

The `multilingual-e5-large` embedding model via Pinecone Inference API is **free**
and included with your Serverless index — no separate billing.

---

## Step 1 — Create a Free Account

1. Go to **[https://app.pinecone.io](https://app.pinecone.io)**
2. Click **Sign Up Free**
3. Sign up with **Google**, **GitHub**, or **email**
4. Verify your email if prompted
5. You land on the Pinecone Dashboard

---

## Step 2 — Get Your API Key

1. In the left sidebar, click **API Keys**
2. You will see a key named `default`
3. Click the **copy icon** next to the key value

Your API key looks like:

```
pcsk_6abc12_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> Do NOT commit this to git. Add `.env` to your `.gitignore`.

---

## Step 3 — Add to Your `.env` File

Open `Agent_v1_python/.env` (create it if it doesn't exist):

```env
# Pinecone Vector DB
PINECONE_API_KEY=pcsk_your_key_here
PINECONE_INDEX_NAME=antigravity-templates
```

`PINECONE_INDEX_NAME` defaults to `antigravity-templates` in the code. Index names must be lowercase with hyphens only.

---

## Step 4 — Install the SDK

```bash
cd "Agent_v1_python"
pip install pinecone>=5.0.0
```

Or install all dependencies at once after updating `requirements.txt`:

```bash
pip install -r requirements.txt
```

Verify:

```bash
python -c "from pinecone import Pinecone; print('Pinecone SDK OK')"
```

---

## Step 5 — Build the RAG Index (One-Time Setup)

This script creates the Pinecone index and uploads all template chunks.
Run this **before starting the agent server** for the first time.

```bash
cd "Agent_v1_python"
python scripts/build_rag_index.py --template nextjs-app --force
```

**Expected output:**

```
Connecting to Pinecone...
Index 'antigravity-templates' not found. Creating serverless index...
  cloud=aws, region=us-east-1, dimension=1024, metric=cosine
Index created successfully.

Loading template: nextjs-app (35 files)
Chunking template files...
Generated 142 chunks

Embedding chunks via Pinecone Inference API (model: multilingual-e5-large)...
  Batch 1/2: 96 chunks embedded
  Batch 2/2: 46 chunks embedded

Upserting 142 vectors to namespace 'nextjs-app'...
Upserted: 142 vectors

=== Index Stats ===
Index name   : antigravity-templates
Total vectors: 142
Namespaces   : nextjs-app (142 vectors)

=== Test Retrieval ===
Query: "dashboard with cards and navigation"
  [0.91]  app/dashboard/page.tsx           (page)
  [0.88]  components/Sidebar.tsx           (component)
  [0.85]  app/layout.tsx                   (layout)

Done. RAG index is ready.
```

---

## Step 6 — Verify in Pinecone Dashboard

1. Go to **[https://app.pinecone.io](https://app.pinecone.io)**
2. Click **Indexes** in the left sidebar
3. You should see **`antigravity-templates`** with status **Ready**
4. Click the index name → click the **Namespaces** tab
5. You should see `nextjs-app` with a vector count

---

## Rebuilding After Template Changes

If you add or modify files inside `templates/nextjs-app/`, rebuild:

```bash
# Rebuild a specific template (recommended)
python scripts/build_rag_index.py --template nextjs-app --force

# Rebuild ALL templates at once
python scripts/build_rag_index.py --force
```

> `--force` deletes the namespace in Pinecone before re-uploading.  
> Without `--force`, the script skips rebuild if the template file hash hasn't changed.

---

## Troubleshooting

### `UnauthorizedException` / `401 Unauthorized`

Your API key is wrong or has extra whitespace.

```bash
# Check it's loaded correctly
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(repr(os.getenv('PINECONE_API_KEY')))"
```

Expected: `'pcsk_...'` (no spaces, no quotes)

---

### `Index not found` when running the agent

The index was not built yet. Run:

```bash
python scripts/build_rag_index.py --template nextjs-app --force
```

---

### `Quota exceeded` / `You have reached the maximum number of indexes`

You already have 1 index from a previous project. You have two options:

**Option A** — Reuse this agent's index name by deleting the old one:

1. Go to Pinecone Dashboard → **Indexes**
2. Click the old index → **Delete Index**
3. Re-run `build_rag_index.py --force`

**Option B** — Rename to match your existing index:

```env
# In .env — use your existing index name
PINECONE_INDEX_NAME=your-existing-index-name
```

Then re-run build script (it will create a new namespace inside the existing index).

---

### `Dimension mismatch` error

You have an old index with `dimension=384` (from the previous FAISS migration).
Delete it in the Pinecone Dashboard and re-run the build script.
The new index uses `dimension=1024` for `multilingual-e5-large`.

---

### Embeddings are slow / timeout during build

Pinecone Inference batches up to **96 inputs per call**. The build script handles this automatically.
If you have a slow network, the batch size can be reduced in the script:

```python
BATCH_SIZE = 32  # reduce from 96 if timing out
```

---

### `PINECONE_API_KEY not set` warning in agent logs

The `.env` file is not being loaded. Make sure you run the agent from the `Agent_v1_python/` directory:

```bash
cd "Agent_v1_python"
uvicorn api.webhook:app --host 0.0.0.0 --port 8000
```

---

## Quick Reference

```bash
# 1. Install SDK
pip install pinecone>=5.0.0

# 2. Set env vars in .env
PINECONE_API_KEY=pcsk_...
PINECONE_INDEX_NAME=antigravity-templates

# 3. Build RAG index (first time)
python scripts/build_rag_index.py --template nextjs-app --force

# 4. Start agent
uvicorn api.webhook:app --host 0.0.0.0 --port 8000
```
