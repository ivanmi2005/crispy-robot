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
ACE_RE = re.compile(r"acestream://([0-9a-fA-F]{40})(?:\b|$)", re.I)
QUERY_RE = re.compile(r"[?&](?:id|content_id)=([0-9a-fA-F]{40})(?:&|$)", re.I)


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

    header = first_header or '#EXTM3U refresh="3600"'
    groups = unique_in_order(all_groups)
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
