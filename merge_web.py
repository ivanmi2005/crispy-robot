"""Fusiona fuentes M3U y genera dos variantes AceStream estables."""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests

BASE = Path(__file__).resolve().parent
REQUEST_FILE = BASE / "merge_request.json"
PUBLISHED_DIR = BASE / "published"
# Listas que se incluyen siempre, además de las elegidas en la web.
FIXED_SOURCES = [
    {"label": "SPORT TV (fija)", "type": "file", "path": "fixed_sources/lista_sport_tv.m3u"},
    {"label": "NEW LOOP (fija)", "type": "file", "path": "fixed_sources/lista_new_loop.m3u"},
]
ACE_RE = re.compile(r"acestream://([0-9a-fA-F]{40})(?:\b|$)", re.I)
QUERY_RE = re.compile(r"[?&](?:id|content_id)=([0-9a-fA-F]{40})(?:&|$)", re.I)
HASH_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{40})(?![0-9a-fA-F])")


@dataclass(slots=True)
class Entry:
    ace_id: str
    metadata_lines: list[str]


def read_source(source: dict, retries: int = 3) -> str:
    if source.get("type") == "file":
        relative = Path(source.get("path", ""))
        path = (BASE / relative).resolve()
        if BASE not in path.parents:
            raise ValueError(f"Ruta fuera del repositorio: {relative}")
        return path.read_text(encoding="utf-8-sig", errors="replace")

    if source.get("type") == "url":
        url = str(source.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"URL no válida: {url!r}")
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                response = requests.get(
                    url,
                    timeout=35,
                    headers={"User-Agent": "crispy-robot-m3u-merger/1.0"},
                )
                response.raise_for_status()
                response.encoding = response.encoding or "utf-8"
                return response.text
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(attempt * 2)
        raise RuntimeError(f"No se pudo descargar {url}: {last_error}")

    raise ValueError(f"Tipo de fuente desconocido: {source.get('type')!r}")


def extract_ace_id(line: str) -> str | None:
    match = ACE_RE.search(line) or QUERY_RE.search(line)
    return match.group(1).lower() if match else None


def bare_entry(line: str) -> Entry | None:
    """Admite texto pegado sin #EXTINF: enlaces sueltos, hashes o «Nombre acestream://id»."""
    ace_id = extract_ace_id(line)
    if not ace_id:
        match = HASH_RE.search(line)
        ace_id = match.group(1).lower() if match else None
    if not ace_id:
        return None
    name = re.sub(r"(?:acestream://|https?://\S*?[?&](?:id|content_id)=)?[0-9a-fA-F]{40}\S*", "", line)
    name = name.strip(" \t,;:|-–—") or f"AceStream {ace_id[:8]}"
    return Entry(ace_id=ace_id, metadata_lines=[f"#EXTINF:-1,{name}"])


def parse_m3u(text: str) -> tuple[str | None, list[str], list[Entry]]:
    header = None
    groups: list[str] = []
    entries: list[Entry] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("#EXTM3U") and header is None:
            header = line
            i += 1
            continue
        if line.startswith("#EXTGRP:"):
            groups.append(line)
            i += 1
            continue
        if not line.startswith("#EXTINF"):
            bare = None if line.startswith("#") else bare_entry(line)
            if bare:
                entries.append(bare)
            i += 1
            continue

        metadata = [line]
        j = i + 1
        ace_id = None
        while j < len(lines):
            candidate = lines[j].strip()
            if candidate.startswith("#EXTINF"):
                break
            if candidate:
                found = extract_ace_id(candidate)
                if found:
                    ace_id = found
                    break
                if candidate.startswith("#"):
                    metadata.append(candidate)
            j += 1

        if ace_id:
            entries.append(Entry(ace_id=ace_id, metadata_lines=metadata))
            i = j + 1
        else:
            i = max(j, i + 1)

    return header, groups, entries


def unique_in_order(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def provider_priority(entry: Entry) -> int:
    """ELCANO primero, después SPORTTV, NEWLOOP y finalmente el resto."""
    text = " ".join(entry.metadata_lines).upper()
    compact = re.sub(r"[^A-Z0-9]+", "", text)

    if "ELCANO" in compact:
        return 0
    if "SPORTTV" in compact:
        return 1
    if "NEWLOOP" in compact:
        return 2
    return 3


def entry_name(entry: Entry) -> str:
    """Nombre visible del canal, sin el «--> PROVEEDOR» del final."""
    line = entry.metadata_lines[0]
    if re.match(r'#EXTINF:[^,"\s]*\s*,', line):
        name = line.split(",", 1)[1]
    elif '",' in line:
        name = line.rsplit('",', 1)[1]
    else:
        name = line.rsplit(",", 1)[-1]
    return re.sub(r"\s*-->.*$", "", name).strip()


def entry_group(entry: Entry) -> str:
    match = re.search(r'group-title="([^"]*)"', entry.metadata_lines[0])
    return match.group(1).strip() if match else ""


QUALITY_WORDS = {"hd", "fhd", "uhd", "sd", "4k", "8k", "720p", "1080p", "1080i", "720", "1080", "50fps", "bar"}


def channel_key(entry: Entry) -> str:
    """Clave para juntar el mismo canal de distintos proveedores (ignora calidad y símbolos)."""
    text = entry_name(entry).casefold().replace("la liga", "laliga")
    text = re.sub(r"(?<=[^\W\d_])(?=\d)", " ", text)  # esport3 → esport 3
    words = [w for w in re.sub(r"[^\w+]+", " ", text).split() if w not in QUALITY_WORDS]
    key = " ".join(words)
    key = re.sub(r"^m\+ (deportes|golf|vamos)\b", r"movistar \1", key)  # mismo canal, otro nombre
    key = {"dazn laliga": "dazn laliga 1"}.get(key, key)
    return key.replace("laliga tv hypermotion", "hypermotion")


BRAND_RE = re.compile(r"^(m\+|movistar|dazn)\s+(.*)$")


def natural_key(text: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def group_titles(groups: list[str]) -> list[str]:
    titles = []
    for line in groups:
        match = re.search(r'group-title="([^"]*)"', line)
        if match and match.group(1).strip() not in titles:
            titles.append(match.group(1).strip())
    return titles


def sort_by_channel(entries: list[Entry], group_order: list[str]) -> list[Entry]:
    """Agrupa por categoría y canal; dentro de cada canal, ELCANO → SPORTTV → NEWLOOP → resto."""
    by_channel: dict[str, list[Entry]] = {}
    for entry in entries:
        by_channel.setdefault(channel_key(entry), []).append(entry)

    info = {}
    for key, items in by_channel.items():
        best = min(items, key=provider_priority)
        info[key] = (best, entry_group(best))

    # Movistar y DAZN con el mismo tema y número (p. ej. «M+ LALIGA 2» y «DAZN LA LIGA 2»)
    # se intercalan por número, con Movistar delante.
    brand_of: dict[str, tuple[str, str, int, int]] = {}
    brands_in: dict[tuple[str, str], set[int]] = {}
    for key, (_, group) in info.items():
        match = BRAND_RE.match(key)
        if not match:
            continue
        rank = 0 if match.group(1) in ("m+", "movistar") else 1
        rest = match.group(2)
        number = re.search(r"(\d+)$", rest)
        topic = rest[: number.start()].strip() if number else rest
        brand_of[key] = (group, topic, int(number.group(1)) if number else 1, rank)
        brands_in.setdefault((group, topic), set()).add(rank)

    def sort_text(key: str) -> str:
        if key in brand_of:
            group, topic, number, rank = brand_of[key]
            if brands_in[(group, topic)] == {0, 1}:
                return f"{topic} {number} {rank}"
        return key

    def order(item: tuple[str, list[Entry]]):
        key, _ = item
        best, group = info[key]
        group_pos = group_order.index(group) if group in group_order else len(group_order)
        return (group_pos, group.casefold(), natural_key(sort_text(key)))

    result: list[Entry] = []
    for _, items in sorted(by_channel.items(), key=order):
        # sorted() es estable: dentro de un mismo proveedor se conserva el orden original.
        result.extend(sorted(items, key=provider_priority))
    return result


def build_m3u(entries: list[Entry], header: str, groups: list[str], mode: str) -> str:
    lines = [header, "#EXTVLCOPT:network-caching=1000", ""]
    if groups:
        lines.extend(groups)
        lines.append("")
    for entry in entries:
        lines.extend(entry.metadata_lines)
        if mode == "local":
            lines.append(f"http://127.0.0.1:6878/ace/getstream?id={entry.ace_id}")
        else:
            lines.append(f"acestream://{entry.ace_id}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    config = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    sources = [source for source in config.get("sources", []) if source.get("enabled", True)]
    # Las fijas van primero: si un canal se repite, se conservan su nombre y logo.
    sources = [source for source in FIXED_SOURCES if (BASE / source["path"]).exists()] + sources
    if not sources:
        raise ValueError("No hay fuentes activas en merge_request.json")

    first_header = None
    all_groups = []
    merged = []
    seen_ids = set()
    source_stats = []

    for index, source in enumerate(sources, start=1):
        label = source.get("label") or f"Fuente {index}"
        header, groups, entries = parse_m3u(read_source(source))
        if first_header is None and header:
            first_header = header
        all_groups.extend(groups)
        added = 0
        duplicates = 0
        for entry in entries:
            if entry.ace_id in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(entry.ace_id)
            merged.append(entry)
            added += 1
        source_stats.append({
            "label": label,
            "type": source.get("type"),
            "found": len(entries),
            "added": added,
            "duplicates": duplicates,
        })
        print(f"{label}: {len(entries)} encontradas, {added} añadidas, {duplicates} duplicadas")

    groups = unique_in_order(all_groups)
    merged = sort_by_channel(merged, group_titles(groups))

    provider_counts = {
        "elcano": sum(provider_priority(entry) == 0 for entry in merged),
        "sporttv": sum(provider_priority(entry) == 1 for entry in merged),
        "newloop": sum(provider_priority(entry) == 2 for entry in merged),
        "resto": sum(provider_priority(entry) == 3 for entry in merged),
    }
    print(
        "Orden: categoría → canal → proveedor. Proveedores: "
        f"ELCANO {provider_counts['elcano']} → "
        f"SPORTTV {provider_counts['sporttv']} → "
        f"NEWLOOP {provider_counts['newloop']} → "
        f"RESTO {provider_counts['resto']}"
    )

    header = first_header or '#EXTM3U refresh="3600"'
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    ace_name = config.get("outputs", {}).get("acestream", "lista_acestream_unificada.m3u")
    local_name = config.get("outputs", {}).get("local", "lista_local_unificada.m3u")

    (PUBLISHED_DIR / ace_name).write_text(
        build_m3u(merged, header, groups, "acestream"), encoding="utf-8", newline="\n"
    )
    (PUBLISHED_DIR / local_name).write_text(
        build_m3u(merged, header, groups, "local"), encoding="utf-8", newline="\n"
    )

    status = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_unique": len(merged),
        "total_found": sum(item["found"] for item in source_stats),
        "total_duplicates": sum(item["duplicates"] for item in source_stats),
        "sort_order": ["CATEGORIA", "CANAL", "ELCANO", "SPORTTV", "NEWLOOP", "RESTO"],
        "provider_counts": provider_counts,
        "sources": source_stats,
        "outputs": {
            "acestream": f"published/{ace_name}",
            "local": f"published/{local_name}",
        },
        "request_id": config.get("request_id"),
    }
    (PUBLISHED_DIR / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
        (PUBLISHED_DIR / "status.json").write_text(
            json.dumps({
                "ok": False,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
