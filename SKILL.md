---
name: ops-cognee-rollout
description: Deploy, repair, harden, and verify OpenClaw + Cognee memory on a Mac mini or shared Cognee server. Use when setting up Cognee from scratch, copying a known-good Cognee setup to another Mac mini, exposing one Mac mini as the Cognee service host for other OpenClaw clients, switching clients to a remote Cognee baseUrl, or troubleshooting Cognee failures involving Docker/Colima, SiliconFlow, embedding dimensions, dataset corruption, recall not injecting, or cognee-openclaw update failures.
---

# OpenClaw Cognee Rollout

Use this skill to reproduce the working Cognee setup that was validated on 2026-03-21.

## What this skill standardizes

- Run Cognee in Docker on one Mac mini
- Use SiliconFlow for both LLM and embedding
- Patch Cognee 0.5.5-local so `bge-m3` works with 1024-dim vectors
- Patch `cognee-openclaw` so update becomes replace-on-failure
- Convert Cognee into a non-slot sidecar when it must coexist with `memory-lancedb-pro`
- Point local or remote OpenClaw clients at the Cognee server
- Verify real recall, not just health

## Canonical known-good shape

- Cognee backend: `0.5.5-local`
- LLM model: `openai/Qwen/Qwen2.5-72B-Instruct`
- Embedding model: `openai/BAAI/bge-m3`
- Tokenizer: `BAAI/bge-m3`
- Search type: `CHUNKS`
- Working dataset pattern: create a fresh dataset when prior datasets were polluted by failed update/cognify runs

## Server vs client mode

### Server mode

Use one Mac mini as the Cognee host.

1. Ensure Colima/Docker works
2. Run Cognee container bound to the LAN, not just localhost
3. Apply the Cognee hotfix script from `scripts/apply_cognee_hotfix.sh`
4. Verify `/health`
5. Open the chosen host/port to LAN or tailnet clients

Use `references/server-mode.md` for the exact environment and run command.

### Client mode

Use another Mac mini as an OpenClaw client that talks to the shared Cognee host.

1. Patch the local `cognee-openclaw` plugin with `scripts/patch_openclaw_cognee_plugin.py`
2. Point `openclaw.json` to the remote `baseUrl`
3. Use a unique dataset name per machine unless you intentionally want a shared dataset
4. Re-index and run recall verification

Use `references/client-mode.md` for the exact config shape.

## Required fixes

### 1) Patch Cognee backend

Run:

```bash
bash skills/openclaw-cognee-rollout/scripts/apply_cognee_hotfix.sh
```

What it fixes:

- suppress embedding `dimensions` in LiteLLM embedding calls
- set Cognee default embedding dimension to `1024`

Without this, SiliconFlow + `bge-m3` fails during cognify/retrieval.

### 2) Patch OpenClaw plugin

Run:

```bash
python3 skills/openclaw-cognee-rollout/scripts/patch_openclaw_cognee_plugin.py
```

What it fixes:

- `update` failure on Cognee 0.5.5 is converted into `replace` behavior (`add new` + `delete old`)

Without this, dirty files often poison datasets and later cognify runs.

## Configure a client quickly

Preferred generic method:

```bash
bash skills/openclaw-cognee-rollout/scripts/onboard_cognee_client.sh \
  --base-url http://SERVER_IP:8000
```

This does not depend on any specific machine name. It auto-generates a dataset name from the local hostname.

Manual method:

```bash
python3 skills/openclaw-cognee-rollout/scripts/configure_openclaw_cognee_client.py \
  --base-url http://SERVER_IP:8000 \
  --dataset-name openclaw-client-name \
  --search-type CHUNKS
```

Then run:

```bash
openclaw cognee index
```

## Verification rule

Do **not** stop at health or index counts.

Always verify all four:

1. `health` works
2. `index` works with `0 errors`
3. add / update / delete work
4. recall actually injects `<cognee_memories>` or `/api/v1/search` returns real memory hits

## Sidecar coexistence mode

Use this mode when `memory-lancedb-pro` must own `plugins.slots.memory` but Cognee still needs to keep its own sync/recall lifecycle.

Rules:

1. `memory-lancedb-pro` must own `plugins.slots.memory`
2. original `cognee-openclaw` must be disabled
3. cloned sidecar plugin (for example `cognee-sidecar-openclaw`) must be enabled
4. sidecar manifest must not declare `kind: "memory"`
5. sidecar setup logic must not write `plugins.slots.memory = "cognee-openclaw"`

Use this helper when you need to generate the sidecar clone deterministically:

```bash
python3 skills/openclaw-cognee-rollout/scripts/make_cognee_sidecar_clone.py --force
```

Use this helper when you need to switch the host between original Cognee memory mode and LanceDB Pro + Cognee sidecar mode:

```bash
python3 skills/openclaw-cognee-rollout/scripts/toggle_cognee_sidecar_mode.py status
python3 skills/openclaw-cognee-rollout/scripts/toggle_cognee_sidecar_mode.py apply
python3 skills/openclaw-cognee-rollout/scripts/toggle_cognee_sidecar_mode.py revert
```

`apply` writes a timestamped backup of `openclaw.json`, sets `plugins.slots.memory = "memory-lancedb-pro"`, enables `memory-lancedb-pro`, disables `cognee-openclaw`, and enables `cognee-sidecar-openclaw`.

`revert` writes a timestamped backup and returns to the original single-slot Cognee shape.

Do not try to keep two `kind: "memory"` plugins enabled and hope only one owns the slot. Runtime auto-disables the non-slot one.

Use:

```bash
python3 skills/openclaw-cognee-rollout/scripts/cognee_smoke_test.py --base-url http://HOST:8000
```

## When to create a fresh dataset

Create a fresh dataset if either is true:

- dataset status stays `DATASET_PROCESSING_ERRORED`
- historical bad updates polluted file pointers or vector schema state

Do not waste time trying to salvage a poisoned dataset unless the user explicitly asks.

## Read these references when needed

- `references/troubleshooting.md` — exact failures and fixes
- `references/server-mode.md` — how to expose one Mac mini as Cognee host
- `references/client-mode.md` — how another Mac mini connects to the shared host
- `references/validated-config.md` — the validated config and command shapes
- `references/sidecar-coexistence.md` — verified `memory-lancedb-pro` + Cognee sidecar coexistence pattern

## Known Cognee user accounts

| User | Password | ID | Notes |
|------|----------|----|-------|
| `default_user@example.com` | `default_password` | `<USER_UUID>` | Auto-created at server init. All data ingested before custom user setup lives here. **This is the data owner on <HOST_NAME>.** |
| `admin2@cognee.ai` | `<YOUR_PASSWORD>` | `<USER_UUID>` | Manually registered. Has empty data directory. |

**Important:** The auth endpoint is `POST /api/v1/auth/login` with `Content-Type: application/x-www-form-urlencoded`. NOT `/api/v1/users/signin`, NOT JSON body.

## Multi-Machine Deployment (多台 Mac Mini 共用)

### User Account 策略

Cognee 有嚴格的多租戶隔離。**切換用戶 = 數據消失**。

| 場景 | 建議 | 原因 |
|------|------|------|
| 同一個人的多台機器 | **統一用 `default_user@example.com`** | 所有數據都在這個用戶名下（884 個 lance 文件） |
| 不同人共用 NAS Cognee | 各建各的用戶（但很少見） | 多租戶隔離 |

### OpenClaw 配置（每台機器的 openclaw.json）

```json
{
  "plugins": {
    "entries": {
      "cognee": {
        "config": {
          "baseUrl": "http://10.10.10.66:8766",
          "username": "default_user@example.com",
          "password": "default_password"
        }
      }
    }
  }
}
```

**⚠️ 關鍵規則：**
- 四台 Mac Mini **必須用同一個賬號**（`default_user@example.com / default_password`）
- **絕對不要**改成 `admin2@cognee.ai` 或其他賬號——會立刻看不到所有數據
- 之前踩過的坑：老大曾用 `admin2@cognee.ai`（ID: 95cf83e5），結果搜索全空，因為數據屬於 `default_user`（ID: f5249267）
- 新機器 onboard 時，直接用 `onboard_cognee_client.sh` 腳本，賬號已內建

### 與 MemOS 的差異

| | MemOS | Cognee |
|---|---|---|
| 隔離粒度 | `user_id` 字段（靈活） | 登錄賬號（嚴格） |
| 切換影響 | 只是搜索過濾不同 | **數據完全不可見** |
| 建議 | 共用 `scott` | 共用 `default_user@example.com` |
| 測試隔離 | 用不同 `user_id` | 用不同 `dataset`（不要換賬號） |

## Common pitfall: user identity mismatch

Cognee has multi-tenant isolation. If you change the username/password in config, the new user cannot see data owned by the old user. See `references/troubleshooting.md` #8 for full diagnosis and fix.

## Monitoring

Use the bench script to run periodic health checks:

```bash
python3 scripts/memory-5a-bench.py
```

This tests all 5 memory layers (L1 LCM, L2 LanceDB, L3 Cognee, L3.5 MemOS, L5 Files) with timing data. Can be set as a cron job for hourly monitoring.

## Known performance issues (2026-03-25)

### TCP connection leak in litellm embedding

**Symptom:** Cognee search latency degrades over time. `netstat` shows hundreds of ESTABLISHED connections to SiliconFlow.

**Root cause:** `litellm.aembedding()` creates new `httpx.AsyncClient` connections per call without pooling. When `asyncio.wait_for` timeout cancels requests, connections leak. Default `litellm.request_timeout = 6000` (100 minutes!) keeps leaked connections alive.

**Fix:** In Cognee container, edit `/app/.venv/lib/python3.12/site-packages/cognee/infrastructure/databases/vector/embeddings/LiteLLMEmbeddingEngine.py`:

```python
# After litellm.set_verbose = False
litellm.request_timeout = 30.0  # Prevent 6000s default causing connection leak
```

**Verification:**
```bash
docker exec cognee python3 -c "
with open('/proc/net/tcp') as f:
    lines = f.readlines()[1:]
    est = sum(1 for l in lines if int(l.split()[3],16)==1)
    print(f'TCP ESTABLISHED: {est}')
"
# Should be <10, not hundreds
```

### GRAPH_COMPLETION search mode causes 30s timeouts

**Symptom:** Cognee search requests timeout at 30s. Logs show vector retrieval + graph projection complete in <1s, but HTTP response never returns.

**Root cause:** Default `search_type = GRAPH_COMPLETION` calls LLM for answer generation after retrieval. If LLM is slow (MiniMax, etc.), the request hangs until gunicorn timeout.

**Fix:** Always use `search_type = "CHUNKS"` (pure vector search, no LLM post-processing). OpenClaw plugin config:

```json
{
  "searchType": "CHUNKS"
}
```

**Impact on stress tests:** The 11% failure rate in 500-round stress tests was caused by this — the test script used default GRAPH_COMPLETION mode, not the CHUNKS mode OpenClaw actually uses. Production is unaffected.

### Single worker limitation

Cognee runs `gunicorn -w 1` (single worker). One stuck request blocks all others. Cannot increase workers because SQLite + LanceDB don't support concurrent writes. This is a design constraint, not a bug.

## NAS Deployment Notes (QNAP/Synology)

### Docker network requirement

Same as MemOS: default bridge network does NOT support container DNS. Create a custom network:

```bash
$DOCKER network create oc-memory
$DOCKER network connect oc-memory oc-cognee-api
$DOCKER network connect oc-memory oc-qdrant
$DOCKER network connect oc-memory oc-neo4j
```

### Cognee user account on NAS

NAS Cognee uses the same default user: `default_user@example.com` / `default_password`. All data belongs to this user. Do NOT switch to a different user unless you want a fresh empty dataset.

### NAS-specific image build notes

- UV timeout: set `UV_HTTP_TIMEOUT=300` in Dockerfile `ENV` (NAS download speed may be slow)
- Pre-pull base image: `docker pull python:3.12-slim` before build
- Embedding dimension: set `EMBEDDING_DIMENSION=1024` for bge-m3 compatibility

### Persisting Cognee patches on NAS

Same strategy as MemOS:
1. Bind mount patched files (e.g., LiteLLMEmbeddingEngine.py)
2. Docker commit as backup image

```bash
$DOCKER cp oc-cognee-api:/app/.venv/lib/python3.12/site-packages/cognee/infrastructure/databases/vector/embeddings/LiteLLMEmbeddingEngine.py /path/to/LiteLLMEmbeddingEngine_patched.py

# Bind mount on recreate:
-v /path/to/LiteLLMEmbeddingEngine_patched.py:/app/.venv/lib/python3.12/site-packages/cognee/infrastructure/databases/vector/embeddings/LiteLLMEmbeddingEngine.py
```

## Operational advice

- Prefer `CHUNKS` before fancier graph-style search while stabilizing rollout
- Prefer one dataset per client machine during rollout; merge later only if needed
- If Colima is running but Docker CLI is broken, fix `DOCKER_HOST` first
- After config changes, re-index from a clean sync index when necessary
- Keep secrets out of notes and skill files
- Periodically restart Cognee container (every 24h) as safety net against FD leaks
- Monitor TCP connections after heavy usage: `docker exec cognee` + `/proc/net/tcp`
