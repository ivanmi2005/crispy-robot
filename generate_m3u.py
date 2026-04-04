"""
Generador standalone de M3U — usado por GitHub Actions y localmente.
Lee config.json + raul_channels.json, descarga fuentes IPFS y genera
los ficheros M3U en la carpeta output/.
"""
import json
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Instala requests: pip install requests")
    sys.exit(1)

BASE = Path(__file__).parent
CONFIG_FILE = BASE / "config.json"
RAUL_FILE = BASE / "raul_channels.json"
OUTPUT_DIR = BASE / "output"


def load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(url: str, retries: int = 3) -> str | None:
    for i in range(retries):
        try:
            r = requests.get(url, timeout=25)
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"  [!] intento {i+1}/{retries} fallido para {url}: {e}")
            time.sleep(2)
    return None


def parse_m3u(text: str) -> list[dict]:
    entries = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#EXTVLCOPT")):
                j += 1
            if j < len(lines):
                url = lines[j].strip()
                ace_hash = None
                if url.startswith("acestream://"):
                    ace_hash = url[12:]
                else:
                    m = re.search(r"[?&]id=([0-9a-fA-F]+)", url)
                    if m:
                        ace_hash = m.group(1)
                if ace_hash:
                    logo = (re.search(r'tvg-logo="([^"]*)"', line) or [None, ""])[1]
                    tvg_id = (re.search(r'tvg-id="([^"]*)"', line) or [None, ""])[1]
                    group = (re.search(r'group-title="([^"]*)"', line) or [None, ""])[1]
                    name_m = re.search(r',\s*(.+)$', line)
                    entries.append({
                        "hash": ace_hash,
                        "name": name_m.group(1).strip() if name_m else "",
                        "tvg_logo": logo,
                        "tvg_id": tvg_id,
                        "group": group,
                    })
                i = j + 1
                continue
        i += 1
    return entries


def build_m3u(entries: list[dict], fmt: str, epg_url: str) -> str:
    lines = [
        f'#EXTM3U url-tvg="{epg_url}" refresh="3600"',
        "#EXTVLCOPT:network-caching=1000",
        "",
    ]
    for e in entries:
        lines.append(
            f'#EXTINF:-1 tvg-logo="{e["tvg_logo"]}" tvg-id="{e["tvg_id"]}" '
            f'group-title="{e["group"]}", {e["name"]}'
        )
        if fmt == "iptv":
            lines.append(f'http://127.0.0.1:6878/ace/getstream?id={e["hash"]}')
        else:
            lines.append(f'acestream://{e["hash"]}')
    return "\n".join(lines)


def main():
    cfg = load_json(CONFIG_FILE)
    raul = load_json(RAUL_FILE)

    seen: set[str] = set()
    all_entries: list[dict] = []

    print("Descargando fuentes IPFS…")
    for source in cfg["sources"]:
        if not source.get("enabled", True):
            print(f"  [skip] {source['name']}")
            continue
        print(f"  → {source['name']}")
        content = fetch(source["url"])
        if not content:
            print(f"  [WARN] No se pudo descargar {source['name']}")
            continue
        for entry in parse_m3u(content):
            if entry["hash"] not in seen:
                seen.add(entry["hash"])
                all_entries.append(entry)
        print(f"       {len(all_entries)} canales acumulados")

    print(f"\nAñadiendo {len([c for c in raul if c.get('enabled', True)])} canales RAUL…")
    for ch in raul:
        if not ch.get("enabled", True):
            continue
        entry = {
            "hash": ch["hash"],
            "name": f"{ch['name']} --> RAUL {ch.get('variant', '')}".strip(),
            "tvg_logo": ch.get("logo", ""),
            "tvg_id": ch.get("tvg_id", ""),
            "group": ch.get("group", "OTROS"),
        }
        if entry["hash"] not in seen:
            seen.add(entry["hash"])
            all_entries.append(entry)

    print(f"\nTotal canales: {len(all_entries)}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    epg_url = cfg["epg_url"]

    for fmt, fname in [("acestream", "lista_acestream.m3u"), ("iptv", "lista_iptv.m3u")]:
        content = build_m3u(all_entries, fmt, epg_url)
        out_path = OUTPUT_DIR / fname
        out_path.write_text(content, encoding="utf-8")
        print(f"  ✓ {out_path}")

    # Generate index.html for GitHub Pages
    index_html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><title>M3U Manager — Listas</title>
<style>
  body{{font-family:system-ui;background:#0f0f13;color:#e0e0f0;max-width:600px;margin:60px auto;padding:0 20px}}
  h1{{color:#6c63ff}}a{{color:#43d9a2}}
  .card{{background:#1a1a24;border:1px solid #2e2e3e;border-radius:10px;padding:20px;margin:14px 0}}
  code{{background:#22222f;padding:3px 8px;border-radius:4px;font-size:.85rem;word-break:break-all}}
  p{{color:#7a7a9a;font-size:.88rem;margin:6px 0 12px}}
</style></head>
<body>
<h1>&#9654; M3U Manager</h1>
<div class="card">
  <h3>acestream:// <small style="color:#7a7a9a">— Acestream Player, fuera de OTT</small></h3>
  <p>Para reproductores como Acestream Player, o software IPTV sin servidor local</p>
  <code><a href="lista_acestream.m3u">lista_acestream.m3u</a></code>
</div>
<div class="card">
  <h3>IPTV (127.0.0.1:6878) <small style="color:#7a7a9a">— OTT Navigator, VLC, Kodi</small></h3>
  <p>Para reproductores IPTV que usen Acestream Engine localmente</p>
  <code><a href="lista_iptv.m3u">lista_iptv.m3u</a></code>
</div>
<p style="margin-top:24px;text-align:center;color:#2e2e3e">
  Generado: {__import__('datetime').datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
  · {len(all_entries)} canales
</p>
</body></html>"""
    (OUTPUT_DIR / "index.html").write_text(index_html, encoding="utf-8")
    print(f"  ✓ {OUTPUT_DIR / 'index.html'}")
    print("\nListo.")


if __name__ == "__main__":
    main()
