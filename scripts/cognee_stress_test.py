#!/usr/bin/env python3
"""Cognee Sidecar 壓測腳本 — 測試 search + add 在持續請求下的延遲穩定性

用法：
  python3 cognee_stress_test.py                                    # 預設 100 輪，NAS
  python3 cognee_stress_test.py --rounds 50                        # 50 輪
  python3 cognee_stress_test.py --url http://10.10.10.66:8766      # 指定 URL
  python3 cognee_stress_test.py --delay 0.5                        # 每輪間隔 500ms
  python3 cognee_stress_test.py --mode search                      # 只測搜索
  python3 cognee_stress_test.py --mode add                         # 只測寫入
  python3 cognee_stress_test.py --mode both                        # 搜索+寫入（預設）
  python3 cognee_stress_test.py --cleanup                          # 跑完後清理測試 dataset

目的：
  1. 驗證搜索在連續請求下是否衰退（litellm 連接泄漏、telemetry 阻塞）
  2. 驗證 add 寫入穩定性
  3. 給出延遲百分位和衰退比，判定 pass/fail

判定標準：
  ✅ PASS: search P95 < 5s + 零錯誤 + 衰退 ≤ 2.0x
  ⚠️ WARN: search P95 < 5s 但衰退 > 2.0x
  ❌ FAIL: search P95 ≥ 5s 或有錯誤
"""

import requests, time, statistics, argparse, sys, json, uuid

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login(base_url: str, username: str, password: str) -> str:
    """Login and return Bearer token."""
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"username": username, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def do_search(base_url: str, headers: dict, query: str, search_type: str = "CHUNKS", timeout: float = 10) -> dict:
    """Execute a search and return {ok, ms, results, error}."""
    t0 = time.time()
    try:
        r = requests.post(
            f"{base_url}/api/v1/search",
            headers=headers,
            json={"query": query, "search_type": search_type},
            timeout=timeout,
        )
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            n = len(data) if isinstance(data, list) else 0
            return {"ok": True, "ms": ms, "results": n, "error": None}
        else:
            return {"ok": False, "ms": ms, "results": 0, "error": f"HTTP {r.status_code}"}
    except requests.Timeout:
        ms = (time.time() - t0) * 1000
        return {"ok": False, "ms": ms, "results": 0, "error": "timeout"}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"ok": False, "ms": ms, "results": 0, "error": str(e)}


def do_add(base_url: str, headers: dict, text: str, dataset: str, timeout: float = 30) -> dict:
    """Add text to a dataset and return {ok, ms, error}."""
    t0 = time.time()
    try:
        r = requests.post(
            f"{base_url}/api/v1/add",
            headers=headers,
            json={"data": text, "dataset_name": dataset},
            timeout=timeout,
        )
        ms = (time.time() - t0) * 1000
        if r.status_code in (200, 201, 202):
            return {"ok": True, "ms": ms, "error": None}
        else:
            return {"ok": False, "ms": ms, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except requests.Timeout:
        ms = (time.time() - t0) * 1000
        return {"ok": False, "ms": ms, "error": "timeout"}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"ok": False, "ms": ms, "error": str(e)}


def delete_dataset(base_url: str, headers: dict, dataset: str) -> bool:
    """Try to delete a dataset. Returns True on success."""
    try:
        r = requests.delete(
            f"{base_url}/api/v1/datasets/{dataset}",
            headers=headers,
            timeout=15,
        )
        return r.status_code in (200, 204, 404)  # 404 = already gone
    except:
        return False

# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

def print_stats(label: str, times: list):
    if not times:
        print(f"  {label}: no data")
        return
    s = sorted(times)
    n = len(s)
    print(f"  {label}:")
    print(f"    Count:  {n}")
    print(f"    Avg:    {statistics.mean(s):.0f}ms")
    print(f"    Min:    {min(s):.0f}ms")
    print(f"    Max:    {max(s):.0f}ms")
    print(f"    Median: {statistics.median(s):.0f}ms")
    print(f"    P95:    {s[int(n * 0.95)]:.0f}ms")
    print(f"    P99:    {s[min(int(n * 0.99), n - 1)]:.0f}ms")

    # Degradation
    bucket = min(25, n // 4)
    if bucket > 0:
        first = statistics.mean(times[:bucket])
        last = statistics.mean(times[-bucket:])
        ratio = last / first if first > 0 else 0
        print(f"    First {bucket} avg: {first:.0f}ms")
        print(f"    Last {bucket} avg:  {last:.0f}ms")
        print(f"    Degradation:  {ratio:.1f}x", end="")
        if ratio <= 1.5:
            print("  ✅ 無衰退")
        elif ratio <= 2.0:
            print("  ⚠️ 輕微衰退")
        elif ratio <= 3.0:
            print("  ⚠️ 明顯衰退")
        else:
            print("  ❌ 嚴重衰退")
    print()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Cognee Sidecar 壓測")
    parser.add_argument("--url", default="http://10.10.10.66:8766",
                        help="Cognee base URL (預設 http://10.10.10.66:8766)")
    parser.add_argument("--rounds", type=int, default=100,
                        help="測試輪次 (預設 100)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="每輪間隔秒數 (預設 0.3)")
    parser.add_argument("--mode", choices=["search", "add", "both"], default="both",
                        help="測試模式: search / add / both (預設 both)")
    parser.add_argument("--search-type", default="CHUNKS",
                        help="搜索類型 (預設 CHUNKS)")
    parser.add_argument("--search-timeout", type=float, default=10,
                        help="搜索超時秒數 (預設 10)")
    parser.add_argument("--username", default="default_user@example.com",
                        help="Cognee 用戶名")
    parser.add_argument("--password", default="default_password",
                        help="Cognee 密碼")
    parser.add_argument("--cleanup", action="store_true",
                        help="測試後清理測試 dataset")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    marker = f"stress-{int(time.time())}"
    dataset = f"stress-test-{uuid.uuid4().hex[:8]}"

    # --- Login ---
    print(f"Cognee 壓測: {args.rounds} 輪 @ {base_url}")
    print(f"  mode={args.mode}, search_type={args.search_type}, delay={args.delay}s")
    print(f"  dataset={dataset}, marker={marker}")
    print()

    print("登入中...", end=" ", flush=True)
    t0 = time.time()
    try:
        token = login(base_url, args.username, args.password)
        print(f"✅ ({(time.time() - t0) * 1000:.0f}ms)")
    except Exception as e:
        print(f"❌ 登入失敗: {e}")
        sys.exit(1)

    headers = auth_headers(token)

    # --- Pre-seed if search mode ---
    if args.mode in ("search", "both"):
        print("寫入種子數據...", end=" ", flush=True)
        seed = do_add(base_url, headers, f"{marker} seed data for stress test benchmark", dataset)
        if seed["ok"]:
            print(f"✅ ({seed['ms']:.0f}ms)")
        else:
            print(f"⚠️ {seed['error']} — 搜索可能無結果但不影響延遲測試")
        print()

    # --- Run tests ---
    search_times = []
    search_errors = 0
    add_times = []
    add_errors = 0

    queries = [
        "memory test", "知识图谱", "search performance", "stress test",
        "machine learning", "数据分析", "user behavior", "系统架构",
        "API performance", "deployment", "configuration", "database",
        "network latency", "optimization", "monitoring", "infrastructure",
    ]

    for i in range(args.rounds):
        # Search
        if args.mode in ("search", "both"):
            q = queries[i % len(queries)]
            r = do_search(base_url, headers, f"{q} {i}", args.search_type, args.search_timeout)
            if r["ok"]:
                search_times.append(r["ms"])
            else:
                search_errors += 1
                if search_errors <= 10:  # Only print first 10
                    print(f"  search #{i}: {r['error']} ({r['ms']:.0f}ms)")

        # Add
        if args.mode in ("add", "both"):
            r = do_add(base_url, headers, f"{marker} round {i} — test data for benchmarking", dataset)
            if r["ok"]:
                add_times.append(r["ms"])
            else:
                add_errors += 1
                if add_errors <= 10:
                    print(f"  add #{i}: {r['error']} ({r['ms']:.0f}ms)")

        # Progress
        if (i + 1) % 25 == 0 or i == 0:
            parts = []
            if search_times:
                parts.append(f"search {len(search_times)}ok/{search_errors}err avg={statistics.mean(search_times[-25:]):.0f}ms")
            if add_times:
                parts.append(f"add {len(add_times)}ok/{add_errors}err avg={statistics.mean(add_times[-25:]):.0f}ms")
            print(f"  [{i + 1}/{args.rounds}] {' | '.join(parts)}")

        time.sleep(args.delay)

    # --- Re-login to avoid token expiry for cleanup ---
    # (tokens might expire during long tests)

    # --- Results ---
    print()
    print("=" * 60)
    print(f"  Cognee 壓測結果 — {args.rounds} 輪 @ {base_url}")
    print(f"  mode={args.mode}, search_type={args.search_type}")
    print("=" * 60)
    print()

    if search_times:
        print_stats("Search", search_times)
    if add_times:
        print_stats("Add", add_times)

    total_errors = search_errors + add_errors
    print(f"  Total errors: {total_errors} (search={search_errors}, add={add_errors})")
    print()

    # --- Cleanup ---
    if args.cleanup:
        print("清理測試 dataset...", end=" ", flush=True)
        try:
            token = login(base_url, args.username, args.password)
            headers = auth_headers(token)
        except:
            pass
        if delete_dataset(base_url, headers, dataset):
            print("✅")
        else:
            print("⚠️ 清理失敗（手動刪除 dataset）")

    # --- Verdict ---
    print()
    exit_code = 0

    if search_times:
        s = sorted(search_times)
        n = len(s)
        p95 = s[int(n * 0.95)]
        bucket = min(25, n // 4)
        ratio = 1.0
        if bucket > 0:
            first = statistics.mean(search_times[:bucket])
            last = statistics.mean(search_times[-bucket:])
            ratio = last / first if first > 0 else 1.0

        if search_errors > 0:
            print(f"❌ FAIL — search 有 {search_errors} 個錯誤")
            exit_code = 1
        elif p95 >= 5000:
            print(f"❌ FAIL — search P95={p95:.0f}ms ≥ 5s（太慢）")
            exit_code = 1
        elif ratio > 2.0:
            print(f"⚠️ WARN — search 衰退 {ratio:.1f}x（P95={p95:.0f}ms）")
            exit_code = 1
        else:
            print(f"✅ PASS — search P95={p95:.0f}ms, 衰退 {ratio:.1f}x")

    if add_times:
        if add_errors > 0:
            print(f"❌ FAIL — add 有 {add_errors} 個錯誤")
            exit_code = 1
        else:
            s = sorted(add_times)
            print(f"✅ PASS — add P95={s[int(len(s) * 0.95)]:.0f}ms, {len(add_times)} 成功")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
