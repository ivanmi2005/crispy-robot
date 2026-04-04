import base64
import json
import re
import time
import uuid
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request, Response

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
RAUL_FILE = BASE_DIR / "raul_channels.json"
SETTINGS_FILE = BASE_DIR / "settings.json"

# Simple in-memory cache: {source_id: (timestamp, content)}
_cache: dict = {}


# ── helpers ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {"github": {}, "cloudflare": {}}
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))


def save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")


def load_raul() -> list:
    return json.loads(RAUL_FILE.read_text(encoding="utf-8"))


def save_raul(channels: list):
    RAUL_FILE.write_text(json.dumps(channels, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_source(source: dict, cache_minutes: int) -> str | None:
    sid = source["id"]
    now = time.time()
    if sid in _cache:
        ts, content = _cache[sid]
        if now - ts < cache_minutes * 60:
            return content
    try:
        r = requests.get(source["url"], timeout=20)
        r.raise_for_status()
        content = r.text
        _cache[sid] = (now, content)
        return content
    except Exception as e:
        print(f"[WARN] Failed to fetch {source['name']}: {e}")
        # Return cached even if stale
        if sid in _cache:
            return _cache[sid][1]
        return None


def parse_m3u_entries(text: str) -> list[dict]:
    """Parse an M3U file into a list of channel dicts."""
    entries = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            meta = line
            # Find the next non-empty, non-comment line as the URL
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#EXTVLCOPT")):
                j += 1
            if j < len(lines):
                url = lines[j].strip()
                # Extract hash from acestream:// or http://127.0.0.1 url
                ace_hash = None
                if url.startswith("acestream://"):
                    ace_hash = url[len("acestream://"):]
                else:
                    m = re.search(r"[?&]id=([0-9a-fA-F]+)", url)
                    if m:
                        ace_hash = m.group(1)
                if ace_hash:
                    # Parse #EXTINF attributes
                    tvg_logo = re.search(r'tvg-logo="([^"]*)"', meta)
                    tvg_id = re.search(r'tvg-id="([^"]*)"', meta)
                    group = re.search(r'group-title="([^"]*)"', meta)
                    # Channel name is after the last comma
                    name_match = re.search(r',\s*(.+)$', meta)
                    entries.append({
                        "hash": ace_hash,
                        "name": name_match.group(1).strip() if name_match else "",
                        "tvg_logo": tvg_logo.group(1) if tvg_logo else "",
                        "tvg_id": tvg_id.group(1) if tvg_id else "",
                        "group": group.group(1) if group else "",
                    })
                i = j + 1
                continue
        i += 1
    return entries


def raul_to_entry(ch: dict) -> dict:
    label = f"{ch['name']} --> RAUL {ch.get('variant', '')}"
    return {
        "hash": ch["hash"],
        "name": label.strip(),
        "tvg_logo": ch.get("logo", ""),
        "tvg_id": ch.get("tvg_id", ""),
        "group": ch.get("group", "OTROS"),
    }


def build_m3u(entries: list[dict], fmt: str, epg_url: str) -> str:
    """Build a merged M3U string. fmt: 'acestream' or 'iptv'"""
    lines = [
        f'#EXTM3U url-tvg="{epg_url}" refresh="3600"',
        "#EXTVLCOPT:network-caching=1000",
        "",
    ]
    # Collect unique groups
    groups_seen = {}
    for e in entries:
        g = e.get("group", "")
        if g and g not in groups_seen:
            groups_seen[g] = e.get("tvg_logo", "")  # reuse logo if available as group logo fallback

    for e in entries:
        logo = e.get("tvg_logo", "")
        tvg_id = e.get("tvg_id", "")
        group = e.get("group", "OTROS")
        name = e.get("name", "")
        h = e["hash"]
        lines.append(
            f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="{group}", {name}'
        )
        if fmt == "iptv":
            lines.append(f"http://127.0.0.1:6878/ace/getstream?id={h}")
        else:
            lines.append(f"acestream://{h}")
    return "\n".join(lines)


def get_merged_entries(cfg: dict, include_raul: bool = True) -> list[dict]:
    """Fetch all enabled sources and merge. Deduplicates by hash."""
    seen_hashes: set[str] = set()
    all_entries: list[dict] = []

    for source in cfg["sources"]:
        if not source.get("enabled", True):
            continue
        content = fetch_source(source, cfg.get("cache_minutes", 30))
        if not content:
            continue
        for entry in parse_m3u_entries(content):
            if entry["hash"] not in seen_hashes:
                seen_hashes.add(entry["hash"])
                all_entries.append(entry)

    if include_raul:
        for ch in load_raul():
            if not ch.get("enabled", True):
                continue
            entry = raul_to_entry(ch)
            if entry["hash"] not in seen_hashes:
                seen_hashes.add(entry["hash"])
                all_entries.append(entry)

    return all_entries


# ── routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(load_config())


@app.route("/api/config/source/<source_id>/toggle", methods=["POST"])
def api_toggle_source(source_id):
    cfg = load_config()
    for s in cfg["sources"]:
        if s["id"] == source_id:
            s["enabled"] = not s.get("enabled", True)
            save_config(cfg)
            return jsonify({"ok": True, "enabled": s["enabled"]})
    return jsonify({"error": "not found"}), 404


@app.route("/api/raul", methods=["GET"])
def api_raul_list():
    return jsonify(load_raul())


@app.route("/api/raul", methods=["POST"])
def api_raul_add():
    data = request.get_json()
    channels = load_raul()
    new_ch = {
        "id": f"raul_{uuid.uuid4().hex[:8]}",
        "name": data.get("name", "").strip(),
        "hash": data.get("hash", "").strip(),
        "group": data.get("group", "OTROS").strip(),
        "logo": data.get("logo", "").strip(),
        "tvg_id": data.get("tvg_id", "").strip(),
        "variant": data.get("variant", "").strip(),
        "enabled": True,
    }
    if not new_ch["name"] or not new_ch["hash"]:
        return jsonify({"error": "name and hash are required"}), 400
    channels.append(new_ch)
    save_raul(channels)
    return jsonify(new_ch), 201


@app.route("/api/raul/<ch_id>", methods=["PUT"])
def api_raul_update(ch_id):
    data = request.get_json()
    channels = load_raul()
    for ch in channels:
        if ch["id"] == ch_id:
            for field in ("name", "hash", "group", "logo", "tvg_id", "variant", "enabled"):
                if field in data:
                    ch[field] = data[field]
            save_raul(channels)
            return jsonify(ch)
    return jsonify({"error": "not found"}), 404


@app.route("/api/raul/<ch_id>", methods=["DELETE"])
def api_raul_delete(ch_id):
    channels = load_raul()
    channels = [c for c in channels if c["id"] != ch_id]
    save_raul(channels)
    return jsonify({"ok": True})


@app.route("/api/raul/bulk", methods=["POST"])
def api_raul_bulk():
    """Parse Discord-style paste and add multiple channels at once."""
    data = request.get_json()
    text = data.get("text", "")
    group = data.get("group", "OTROS")
    channels = load_raul()
    existing_hashes = {c["hash"] for c in channels}

    added = []
    # Pattern: emoji+text line followed by a 40-char hex hash line
    lines = [l.strip() for l in text.splitlines()]
    i = 0
    while i < len(lines):
        line = lines[i]
        # Check if next line is a hash
        if i + 1 < len(lines):
            candidate = lines[i + 1].strip()
            if re.fullmatch(r"[0-9a-fA-F]{40}", candidate):
                # Clean emoji and flags from name
                name = re.sub(r'[^\x20-\x7E\u00C0-\u024F]', '', line).strip()
                # Remove leading/trailing special chars
                name = re.sub(r'^[\s\W]+', '', name).strip()
                variant_match = re.search(r'[Vv]\s*(\d+)', line)
                variant = f"V{variant_match.group(1)}" if variant_match else ""
                if name and candidate not in existing_hashes:
                    new_ch = {
                        "id": f"raul_{uuid.uuid4().hex[:8]}",
                        "name": name,
                        "hash": candidate,
                        "group": group,
                        "logo": "",
                        "tvg_id": "",
                        "variant": variant,
                        "enabled": True,
                    }
                    channels.append(new_ch)
                    existing_hashes.add(candidate)
                    added.append(new_ch)
                i += 2
                continue
        i += 1

    save_raul(channels)
    return jsonify({"added": len(added), "channels": added})


@app.route("/m3u")
def serve_m3u():
    """Serve the merged M3U file."""
    fmt = request.args.get("format", "acestream")
    if fmt not in ("acestream", "iptv"):
        fmt = "acestream"

    cfg = load_config()
    entries = get_merged_entries(cfg)
    content = build_m3u(entries, fmt, cfg["epg_url"])

    filename = f"merged_{fmt}.m3u"
    return Response(
        content,
        mimetype="application/x-mpegurl",
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


@app.route("/api/preview")
def api_preview():
    """Return merged entries as JSON for the UI preview."""
    cfg = load_config()
    entries = get_merged_entries(cfg)
    # Group by group-title
    groups: dict[str, list] = {}
    for e in entries:
        g = e.get("group", "SIN GRUPO")
        groups.setdefault(g, []).append(e)
    return jsonify({
        "total": len(entries),
        "groups": [{"name": k, "channels": v} for k, v in sorted(groups.items())],
    })


@app.route("/api/cache/clear", methods=["POST"])
def api_clear_cache():
    _cache.clear()
    return jsonify({"ok": True})


@app.route("/api/raul/reorder", methods=["POST"])
def api_raul_reorder():
    """Save new order. Body: {"order": ["id1", "id2", ...]}"""
    data = request.get_json()
    new_order = data.get("order", [])
    channels = load_raul()
    id_map = {ch["id"]: ch for ch in channels}
    reordered = [id_map[cid] for cid in new_order if cid in id_map]
    # Append any not included in the order list
    ordered_ids = set(new_order)
    for ch in channels:
        if ch["id"] not in ordered_ids:
            reordered.append(ch)
    save_raul(reordered)
    return jsonify({"ok": True, "count": len(reordered)})


@app.route("/api/raul/batch-delete", methods=["POST"])
def api_raul_batch_delete():
    data = request.get_json()
    ids_to_delete = set(data.get("ids", []))
    channels = load_raul()
    channels = [c for c in channels if c["id"] not in ids_to_delete]
    save_raul(channels)
    return jsonify({"ok": True, "deleted": len(ids_to_delete)})


@app.route("/api/raul/duplicates")
def api_raul_duplicates():
    """Find channels within RAUL that share the same hash."""
    channels = load_raul()
    hash_map: dict = {}
    for ch in channels:
        hash_map.setdefault(ch["hash"], []).append(ch)
    dupes = [{"hash": h, "channels": chs} for h, chs in hash_map.items() if len(chs) > 1]
    return jsonify({"duplicates": dupes, "total": len(dupes)})


@app.route("/api/raul/duplicates/vs-sources")
def api_raul_vs_sources():
    """Find RAUL channels whose hash already exists in the IPFS sources."""
    cfg = load_config()
    raul = load_raul()
    raul_map = {ch["hash"]: ch for ch in raul}

    matches = []
    seen: set[str] = set()
    for source in cfg["sources"]:
        if not source.get("enabled", True):
            continue
        content = fetch_source(source, cfg.get("cache_minutes", 30))
        if not content:
            continue
        for entry in parse_m3u_entries(content):
            h = entry["hash"]
            if h in raul_map and h not in seen:
                seen.add(h)
                matches.append({
                    "raul": raul_map[h],
                    "source": {**entry, "source_name": source["name"]},
                })
    return jsonify({"matches": matches, "total": len(matches)})


@app.route("/api/sources/channels")
def api_sources_channels():
    """Return all channels parsed from enabled IPFS sources (for the picker)."""
    cfg = load_config()
    all_entries = []
    for source in cfg["sources"]:
        if not source.get("enabled", True):
            continue
        content = fetch_source(source, cfg.get("cache_minutes", 30))
        if not content:
            continue
        for entry in parse_m3u_entries(content):
            all_entries.append({**entry, "source_name": source["name"]})
    return jsonify(all_entries)


# ── Settings ─────────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    s = load_settings()
    # Never expose tokens to the frontend — just say if they're set
    safe = {
        "github": {
            "owner": s.get("github", {}).get("owner", ""),
            "repo":  s.get("github", {}).get("repo", ""),
            "branch": s.get("github", {}).get("branch", "main"),
            "token_set": bool(s.get("github", {}).get("token")),
        },
        "cloudflare": {
            "account_id": s.get("cloudflare", {}).get("account_id", ""),
            "worker_name": s.get("cloudflare", {}).get("worker_name", "m3u-manager"),
            "token_set": bool(s.get("cloudflare", {}).get("api_token")),
        },
    }
    return jsonify(safe)


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json()
    s = load_settings()
    gh = data.get("github", {})
    cf = data.get("cloudflare", {})
    s.setdefault("github", {}).update({
        "owner":  gh.get("owner", s["github"].get("owner", "")),
        "repo":   gh.get("repo",  s["github"].get("repo", "")),
        "branch": gh.get("branch", s["github"].get("branch", "main")),
    })
    if gh.get("token"):
        s["github"]["token"] = gh["token"]
    s.setdefault("cloudflare", {}).update({
        "account_id":  cf.get("account_id",  s["cloudflare"].get("account_id", "")),
        "worker_name": cf.get("worker_name", s["cloudflare"].get("worker_name", "m3u-manager")),
    })
    if cf.get("api_token"):
        s["cloudflare"]["api_token"] = cf["api_token"]
    save_settings(s)
    return jsonify({"ok": True})


# ── GitHub publish ────────────────────────────────────────────────────────────

def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@app.route("/api/publish/github", methods=["POST"])
def api_publish_github():
    s = load_settings()
    gh = s.get("github", {})
    token  = gh.get("token", "")
    owner  = gh.get("owner", "")
    repo   = gh.get("repo", "")
    branch = gh.get("branch", "main")

    if not all([token, owner, repo]):
        return jsonify({"error": "Configura GitHub (usuario, repo y token) en Ajustes primero."}), 400

    headers = _gh_headers(token)
    base_url = f"https://api.github.com/repos/{owner}/{repo}/contents"

    # Files to push: raul_channels.json + config.json
    files = {
        "raul_channels.json": RAUL_FILE.read_bytes(),
        "config.json":        CONFIG_FILE.read_bytes(),
    }

    results = []
    for filename, raw_content in files.items():
        url = f"{base_url}/{filename}?ref={branch}"
        # Get current SHA (needed for update)
        get_r = requests.get(url, headers=headers, timeout=10)
        sha = get_r.json().get("sha") if get_r.ok else None

        body: dict = {
            "message": f"chore: actualizar {filename} via M3U Manager",
            "content": base64.b64encode(raw_content).decode(),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha

        put_r = requests.put(url, headers=headers, json=body, timeout=15)
        if put_r.ok:
            results.append({"file": filename, "status": "ok"})
        else:
            results.append({"file": filename, "status": "error", "detail": put_r.json().get("message", "")})

    errors = [r for r in results if r["status"] == "error"]
    if errors:
        return jsonify({"error": errors[0]["detail"], "results": results}), 500

    pages_url = f"https://{owner}.github.io/{repo}"
    return jsonify({
        "ok": True,
        "results": results,
        "pages_url": pages_url,
        "message": "Archivos publicados. GitHub Actions generará las listas en ~1 minuto.",
    })


@app.route("/api/publish/github/status", methods=["GET"])
def api_github_status():
    """Devuelve el estado del último workflow run."""
    s = load_settings()
    gh = s.get("github", {})
    token = gh.get("token", "")
    owner = gh.get("owner", "")
    repo  = gh.get("repo", "")
    if not all([token, owner, repo]):
        return jsonify({"error": "GitHub no configurado"}), 400
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs?per_page=3"
    r = requests.get(url, headers=_gh_headers(token), timeout=10)
    if not r.ok:
        return jsonify({"error": r.json().get("message", "Error")}), r.status_code
    runs = r.json().get("workflow_runs", [])
    if not runs:
        return jsonify({"runs": []})
    return jsonify({
        "runs": [
            {
                "id": run["id"],
                "name": run["name"],
                "status": run["status"],
                "conclusion": run["conclusion"],
                "updated_at": run["updated_at"],
                "html_url": run["html_url"],
            }
            for run in runs[:3]
        ]
    })


# ── Cloudflare Worker deploy ──────────────────────────────────────────────────

def _build_worker_script(cfg: dict, raul: list, gh_owner: str, gh_repo: str, gh_branch: str) -> str:
    """Build the Worker JS that fetches raul_channels.json from GitHub raw."""
    sources = [s for s in cfg["sources"] if s.get("enabled")]
    raul_raw_url = f"https://raw.githubusercontent.com/{gh_owner}/{gh_repo}/{gh_branch}/raul_channels.json"

    sources_json = json.dumps(
        [{"id": s["id"], "name": s["name"], "url": s["url"], "format": s["format"]} for s in sources],
        indent=2,
    )
    epg_url = cfg["epg_url"]

    return f"""// M3U Manager — Cloudflare Worker
// Los canales RAUL se leen en tiempo real desde GitHub.
// Para actualizar: solo haz push a GitHub, sin tocar este Worker.

const EPG_URL = "{epg_url}";
const SOURCES = {sources_json};
const RAUL_URL = "{raul_raw_url}";
const CACHE_TTL = 1800; // 30 min

export default {{
  async fetch(request, env, ctx) {{
    const url = new URL(request.url);
    if (url.pathname === "/m3u") {{
      const fmt = url.searchParams.get("format") || "acestream";
      const entries = await getMergedEntries(ctx);
      return new Response(buildM3U(entries, fmt), {{
        headers: {{
          "Content-Type": "application/x-mpegurl",
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": "public, max-age=900",
        }},
      }});
    }}
    return new Response(
      "M3U Manager Worker\\nUsa /m3u?format=acestream o /m3u?format=iptv",
      {{ headers: {{ "Content-Type": "text/plain" }} }}
    );
  }},
}};

async function getMergedEntries(ctx) {{
  const seen = new Set();
  const all = [];

  // Fetch IPFS sources + raul channels en paralelo
  const [sourceResults, raulChannels] = await Promise.all([
    Promise.allSettled(SOURCES.map(s => fetchCached(s.url, s.id, ctx))),
    fetchRaul(ctx),
  ]);

  for (const result of sourceResults) {{
    if (result.status === "fulfilled" && result.value) {{
      for (const e of parseM3U(result.value)) {{
        if (!seen.has(e.hash)) {{ seen.add(e.hash); all.push(e); }}
      }}
    }}
  }}

  for (const ch of raulChannels) {{
    if (!ch.enabled) continue;
    const entry = {{
      hash: ch.hash,
      name: (ch.name + " --> RAUL " + (ch.variant || "")).trim(),
      tvg_logo: ch.logo || "",
      tvg_id: ch.tvg_id || "",
      group: ch.group || "OTROS",
    }};
    if (!seen.has(entry.hash)) {{ seen.add(entry.hash); all.push(entry); }}
  }}

  return all;
}}

async function fetchRaul(ctx) {{
  try {{
    const r = await fetchCachedRaw(RAUL_URL, "raul_channels", ctx, 300);
    return r ? JSON.parse(r) : [];
  }} catch (e) {{
    return [];
  }}
}}

async function fetchCached(url, id, ctx) {{
  return fetchCachedRaw(url, id, ctx, CACHE_TTL);
}}

async function fetchCachedRaw(url, cacheKey, ctx, ttl) {{
  const key = new Request("https://cache.m3u.internal/" + cacheKey);
  const cache = caches.default;
  let r = await cache.match(key);
  if (r) return r.text();
  r = await fetch(url, {{ cf: {{ cacheTtl: ttl }} }});
  if (!r.ok) return null;
  const text = await r.text();
  ctx.waitUntil(
    cache.put(key, new Response(text, {{
      headers: {{ "Cache-Control": `public, max-age=${{ttl}}` }},
    }}))
  );
  return text;
}}

function parseM3U(text) {{
  const entries = [];
  const lines = text.split("\\n");
  let i = 0;
  while (i < lines.length) {{
    const line = lines[i].trim();
    if (line.startsWith("#EXTINF")) {{
      let j = i + 1;
      while (j < lines.length && (!lines[j].trim() || lines[j].trim().startsWith("#EXTVLC"))) j++;
      if (j < lines.length) {{
        const u = lines[j].trim();
        let hash = null;
        if (u.startsWith("acestream://")) hash = u.slice(12);
        else {{ const m = u.match(/[?&]id=([0-9a-fA-F]+)/); if (m) hash = m[1]; }}
        if (hash) {{
          entries.push({{
            hash,
            name:     (line.match(/,\\s*(.+)$/) || [])[1]?.trim() || "",
            tvg_logo: (line.match(/tvg-logo="([^"]*)"/) || [])[1] || "",
            tvg_id:   (line.match(/tvg-id="([^"]*)"/) || [])[1] || "",
            group:    (line.match(/group-title="([^"]*)"/) || [])[1] || "",
          }});
        }}
        i = j + 1;
        continue;
      }}
    }}
    i++;
  }}
  return entries;
}}

function buildM3U(entries, fmt) {{
  const lines = [
    `#EXTM3U url-tvg="${{EPG_URL}}" refresh="3600"`,
    "#EXTVLCOPT:network-caching=1000",
    "",
  ];
  for (const e of entries) {{
    lines.push(`#EXTINF:-1 tvg-logo="${{e.tvg_logo}}" tvg-id="${{e.tvg_id}}" group-title="${{e.group}}", ${{e.name}}`);
    lines.push(fmt === "iptv"
      ? `http://127.0.0.1:6878/ace/getstream?id=${{e.hash}}`
      : `acestream://${{e.hash}}`);
  }}
  return lines.join("\\n");
}}
"""


@app.route("/api/publish/worker", methods=["POST"])
def api_publish_worker():
    s = load_settings()
    cf = s.get("cloudflare", {})
    gh = s.get("github", {})
    token      = cf.get("api_token", "")
    account_id = cf.get("account_id", "")
    worker     = cf.get("worker_name", "m3u-manager")

    if not all([token, account_id]):
        return jsonify({"error": "Configura Cloudflare (account_id y api_token) en Ajustes primero."}), 400

    gh_owner  = gh.get("owner", "TU_USUARIO")
    gh_repo   = gh.get("repo", "m3u-manager")
    gh_branch = gh.get("branch", "main")

    cfg  = load_config()
    raul = load_raul()
    script = _build_worker_script(cfg, raul, gh_owner, gh_repo, gh_branch)

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/workers/scripts/{worker}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/javascript",
    }
    r = requests.put(url, headers=headers, data=script.encode(), timeout=20)

    if r.ok:
        worker_url = f"https://{worker}.{gh_owner.lower()}.workers.dev"
        return jsonify({
            "ok": True,
            "worker_url": worker_url,
            "message": "Worker desplegado correctamente.",
        })
    else:
        data = r.json()
        errors = data.get("errors", [{}])
        return jsonify({"error": errors[0].get("message", "Error desconocido"), "detail": data}), 500


@app.route("/api/publish/worker/script", methods=["GET"])
def api_worker_script():
    """Devuelve el Worker script sin desplegarlo (para copiar manualmente)."""
    s = load_settings()
    gh = s.get("github", {})
    gh_owner  = gh.get("owner", "TU_USUARIO")
    gh_repo   = gh.get("repo",  "m3u-manager")
    gh_branch = gh.get("branch", "main")
    cfg  = load_config()
    raul = load_raul()
    script = _build_worker_script(cfg, raul, gh_owner, gh_repo, gh_branch)
    return Response(script, mimetype="text/plain")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
