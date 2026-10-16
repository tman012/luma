"""PR #3 typo-tolerant search e2e checks.

Start the mock upstreams + Luma first (see tests/e2e/README.md).
Timings are appended to $LUMA_E2E_SHOTS/timings.jsonl.
"""
import json, re, time, pytest
from conftest import BASE, SHOTS

pytestmark = pytest.mark.e2e

TYPOS = {"Openheimer": "Oppenheimer", "Interstelar": "Interstellar",
         "Godfahter": "The Godfather", "Shawshank Redemtion": "The Shawshank Redemption"}
TOP_N = 3
DID_YOU_MEAN = re.compile(r"did\s+you\s+mean", re.I)

def record(**kw):
    with open(f"{SHOTS}/timings.jsonl", "a") as f:
        f.write(json.dumps(kw) + "\n")

def timed_get(api, url):
    t0 = time.perf_counter(); r = api.get(url); ms = (time.perf_counter() - t0) * 1000
    assert r.ok, f"{url} -> {r.status} {r.text()[:200]}"
    return r.json(), ms

def titles(items):
    return [i.get("title") or i.get("name") for i in items]

# ---------- TMDb discovery (/search) ----------
@pytest.mark.parametrize("typo,want", TYPOS.items())
def test_tmdb_discovery_typo(api, typo, want):
    d, ms = timed_get(api, f"/search?q={typo}&type=all")
    record(check="tmdb_typo", q=typo, wall_ms=round(ms, 1), top=titles(d["movies"])[:5])
    assert want in titles(d["movies"])[:TOP_N]
    assert not DID_YOU_MEAN.search(json.dumps(d))

# ---------- Library: endpoint the UI actually calls ----------
@pytest.mark.parametrize("typo,want", TYPOS.items())
def test_library_ui_endpoint_typo(api, typo, want):
    d, ms = timed_get(api, f"/api/library/search?q={typo}")
    record(check="library_ui_typo", q=typo, wall_ms=round(ms, 1), top=titles(d["movies"])[:5])
    assert want in titles(d["movies"])[:TOP_N], "UI library search (/api/library/search) is not typo tolerant"

# ---------- Library: fast in-memory index ----------
@pytest.mark.parametrize("typo,want", list(TYPOS.items()) + [("amelie", "Amélie")])
def test_library_fast_typo(api, typo, want):
    d, ms = timed_get(api, f"/api/library/search/fast?q={typo}&limit=20")
    record(check="library_fast_typo", q=typo, wall_ms=round(ms, 1), server_ms=d["elapsed_ms"],
           index=d["index_size"], top=titles(d["results"])[:5])
    assert want in titles(d["results"])[:TOP_N]

def test_tmdb_amelie(api):
    d, _ = timed_get(api, "/search?q=amelie&type=all")
    assert "Amélie" in titles(d["movies"])[:TOP_N]

# ---------- Ranking ----------
@pytest.mark.parametrize("q,first", [("Up", "Up"), ("Oppenheimer", "Oppenheimer"),
                                     ("The Godfather", "The Godfather"), ("Interst", "Interstellar")])
def test_exact_and_prefix_first(api, q, first):
    d, _ = timed_get(api, f"/api/library/search/fast?q={q}&limit=10")
    t = titles(d["results"]); assert t[0] == first, t
    if q == "Up": assert t.index("Up") < t.index("Upgrade")
    d2, _ = timed_get(api, f"/search?q={q}&type=all")
    assert titles(d2["movies"])[0] == first, titles(d2["movies"])

@pytest.mark.parametrize("q", ["u", "Up", "in", "sh"])
def test_short_queries_prefix_only(api, q):
    d, _ = timed_get(api, f"/api/library/search/fast?q={q}&limit=200")
    bad = [t for t in titles(d["results"]) if not any(w.lower().startswith(q.lower()) for w in [t] + t.split())]
    assert not bad, f"non-prefix matches for {q!r}: {bad[:5]}"

def test_corrected_after_direct_and_deduped(api, mock):
    q = "Godfahter Part"   # mock TMDb returns a weak direct hit for 'part'
    direct = mock.get(f"/tmdb/search/multi?query={q}").json()["results"]
    direct_ids = [m["id"] for m in direct if m["media_type"] == "movie"]
    d, _ = timed_get(api, f"/search?q={q}&type=all")
    ids = [m["id"] for m in d["movies"]]
    assert len(ids) == len(set(ids)), "duplicate ids"
    assert direct_ids, "test setup: expected a direct hit"
    assert ids[:len(direct_ids)] == direct_ids, f"direct {direct_ids} should lead, got {ids}"
    assert len(ids) > len(direct_ids), "expected corrected results appended"
    assert not DID_YOU_MEAN.search(json.dumps(d))
    assert all("_corrected" not in m for m in d["movies"])

# ---------- Performance (10k library) ----------
def test_fast_search_large_library_under_50ms(api):
    d, _ = timed_get(api, "/api/library/search/fast?q=x")
    if d["index_size"] < 5000: pytest.skip(f"index only {d['index_size']} items; start mock with MOCK_SEED_ITEMS=10000")
    worst = []
    for q in ["Openheimer", "Interstelar", "Godfahter", "Shawshank Redemtion", "amelie", "Up", "Shadow Rivr Nite"]:
        runs = [timed_get(api, f"/api/library/search/fast?q={q}&limit=20")[0]["elapsed_ms"] for _ in range(5)]
        runs.sort(); med = runs[2]; worst.append((q, med, runs[-1]))
        record(check="perf_10k", q=q, index=d["index_size"], server_ms_median=med, server_ms_max=runs[-1])
    over = [w for w in worst if w[1] >= 50]
    assert not over, f"median >=50ms: {over}"

# ---------- UI (phone + desktop) ----------
def open_search(page):
    page.goto(BASE + "/"); page.wait_for_load_state("networkidle")
    name = "Search" if page.viewport_name == "phone" else "Open search"
    page.get_by_role("button", name=name, exact=True).first.click()
    return page.locator("input[type=search]:visible").first

@pytest.mark.parametrize("typo,want", TYPOS.items())
def test_ui_typo_search(page, typo, want):
    inp = open_search(page)
    t0 = time.perf_counter(); inp.fill(typo)
    hit = page.get_by_text(want, exact=True).first
    shot = f"{SHOTS}/{page.viewport_name}_{typo.replace(' ', '_')}.png"
    try:
        hit.wait_for(state="visible", timeout=5000)
    finally:
        page.wait_for_timeout(800); page.screenshot(path=shot)
    ms = (time.perf_counter() - t0) * 1000 - 800
    body = page.inner_text("body")
    record(check="ui_typo", viewport=page.viewport_name, q=typo, visible_ms=round(ms, 1), shot=shot,
           no_results_banner=("No results found" in body))
    assert not DID_YOU_MEAN.search(body)
    assert f"No results found for “{typo}”" not in body, "contradictory 'No results found' shown next to a result"

def test_ui_exact_up_first(page):
    inp = open_search(page); inp.fill("Up")
    page.get_by_text("Upgrade", exact=True).first.wait_for(timeout=5000)
    page.wait_for_timeout(800)
    page.screenshot(path=f"{SHOTS}/{page.viewport_name}_Up.png")
    body = page.inner_text("body")
    up = page.get_by_text("Up", exact=True).first.bounding_box()
    upg = page.get_by_text("Upgrade", exact=True).first.bounding_box()
    assert (up["y"], up["x"]) < (upg["y"], upg["x"]), "Upgrade rendered before Up"

# ---------- PWA ----------
def test_pwa_manifest_and_sw(page):
    m = page.request.get(BASE + "/manifest.json"); assert m.ok
    mj = m.json(); assert mj["name"] and mj["start_url"] and mj["icons"]
    for icon in mj["icons"]:
        assert page.request.get(BASE + icon["src"]).ok, icon["src"]
    assert page.request.get(BASE + "/sw.js").ok
    page.goto(BASE + "/"); page.wait_for_load_state("networkidle")
    assert page.locator('link[rel="manifest"]').count() >= 1
    scope = page.evaluate("""async () => { const r = await navigator.serviceWorker.getRegistration();
        if (r) return r.scope; const n = await Promise.race([navigator.serviceWorker.ready,
        new Promise(res => setTimeout(() => res(null), 5000))]); return n && n.scope; }""")
    assert scope, "no service worker registered"

# ---------- Hero cache ----------
def test_hero_static_served(api):
    import os, pathlib
    # Use configured HERO_CACHE_DIR from environment
    hero_dir = os.environ.get("HERO_CACHE_DIR")
    if not hero_dir:
        # Default: LUMA_CACHE_DIR/hero
        luma_cache = os.environ.get("LUMA_CACHE_DIR", "/app/cache")
        hero_dir = f"{luma_cache}/hero"
    
    p = pathlib.Path(hero_dir) / "__qa_probe.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8\xff\xe0qa")
    try:
        # Hero images are now served from the cache directory via Flask static_folder config
        # The test needs to know the URL path where they're served
        # For now, assume standard /static/hero/ path (configured in Flask)
        r = api.get("/static/hero/__qa_probe.jpg"); assert r.ok, r.status
    finally:
        p.unlink(missing_ok=True)

# ---------- TMDb 429 (keep last: Luma backs off for Retry-After) ----------
def test_tmdb_rate_limited_graceful(api, mock):
    mock.get("/__ctl?mode=429&reset=1")
    try:
        d, ms = timed_get(api, "/search?q=Openheimer&type=all")
        hits1 = mock.get("/__ctl").json()["hits"]
        d2, ms2 = timed_get(api, "/search?q=Interstelar&type=all")
        hits2 = mock.get("/__ctl").json()["hits"] - hits1
        record(check="tmdb_429", wall_ms=[round(ms, 1), round(ms2, 1)], upstream_hits=[hits1, hits2])
        assert ms < 3000 and ms2 < 3000
        assert hits1 <= 4, f"correction retried while rate limited ({hits1} upstream calls)"
        assert not DID_YOU_MEAN.search(json.dumps(d))
        fast, _ = timed_get(api, "/api/library/search/fast?q=Openheimer")  # library unaffected
        assert titles(fast["results"])[0] == "Oppenheimer"
    finally:
        mock.get("/__ctl?mode=ok")

