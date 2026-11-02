"""Item and character database: maps in-game names to model (PAC) files.

Parses the localization DB, iteminfo.pabgb, and .app.xml appearance files
to build lookups from display names → PAC filenames.

Usage:
    from item_db import build_item_index, build_character_index
    index = build_item_index(game_dir, pamt_0009_entries, progress_fn)
    char_index = build_character_index(pamt_0009_entries, index.prefab_pac_map, progress_fn)
"""

import os
import re
import struct
from dataclasses import dataclass, field

import lz4.block

from paz_parse import parse_pamt
from paz_crypto import decrypt, hashlittle


# ── Data structures ───────────────────────────────────────────────

@dataclass
class ItemRecord:
    item_id: int
    internal_name: str
    display_name: str = ""
    description: str = ""
    prefab_hashes: list[int] = field(default_factory=list)
    pac_files: list[str] = field(default_factory=list)


@dataclass
class ItemIndex:
    items: list[ItemRecord]
    pac_to_items: dict[str, list[ItemRecord]]   # pac_basename → items
    prefab_pac_map: dict = field(default_factory=dict)  # prefab_basename → [pac_basenames]


@dataclass
class CharacterRecord:
    app_name: str           # e.g. "cd_phm_macduff_00000"
    display_label: str      # e.g. "macduff" or "bear_00000"
    search_key: str         # lowercase combined name for search
    pac_files: list[str] = field(default_factory=list)   # resolved pac filenames


@dataclass
class CharacterIndex:
    characters: list[CharacterRecord]
    pac_to_chars: dict[str, list[CharacterRecord]]  # pac_basename → characters


# ── Localization DB ───────────────────────────────────────────────

def parse_localization(game_dir: str, progress_fn=None) -> dict[str, str]:
    """Parse English localization DB → dict of loc_id_string → display_text."""
    if progress_fn:
        progress_fn("Loading English localization...")

    dir_0020 = os.path.join(game_dir, "0020")
    pamt_path = os.path.join(dir_0020, "0.pamt")
    if not os.path.isfile(pamt_path):
        return {}

    entries = parse_pamt(pamt_path, paz_dir=dir_0020)
    loc_entries = [e for e in entries if "localizationstring_eng" in e.path.lower()
                   or e.path.lower().endswith(".paloc")]
    if not loc_entries:
        return {}

    loc_dict = {}
    for loc_entry in loc_entries:
        try:
            read_size = loc_entry.comp_size if loc_entry.compressed else loc_entry.orig_size
            with open(loc_entry.paz_file, 'rb') as f:
                f.seek(loc_entry.offset)
                raw = f.read(read_size)

            dec = decrypt(raw, os.path.basename(loc_entry.path))
            data = lz4.block.decompress(dec, uncompressed_size=loc_entry.orig_size) if loc_entry.compressed else dec

            pos = 0
            while pos + 8 < len(data):
                slen = struct.unpack_from('<I', data, pos)[0]
                if slen == 0 or slen > 50000 or pos + 4 + slen > len(data):
                    pos += 1
                    continue

                s_bytes = data[pos + 4:pos + 4 + slen]

                if 6 <= slen <= 25 and all(0x30 <= b <= 0x39 for b in s_bytes):
                    id_str = s_bytes.decode('ascii')
                    text_pos = pos + 4 + slen
                    if text_pos + 4 < len(data):
                        text_len = struct.unpack_from('<I', data, text_pos)[0]
                        if 0 < text_len < 50000 and text_pos + 4 + text_len <= len(data):
                            text = data[text_pos + 4:text_pos + 4 + text_len].decode(
                                'utf-8', errors='replace')
                            loc_dict[id_str] = text
                            pos = text_pos + 4 + text_len
                            continue

                pos += 1
        except Exception:
            continue

    return loc_dict


# ── iteminfo.pabgb ───────────────────────────────────────────────

def parse_iteminfo(game_dir: str, loc_dict: dict[str, str],
                   progress_fn=None) -> list[ItemRecord]:
    """Parse item database → list of ItemRecord with resolved display names."""
    if progress_fn:
        progress_fn("Loading item database...")

    dir_0008 = os.path.join(game_dir, "0008")
    pamt_path = os.path.join(dir_0008, "0.pamt")
    if not os.path.isfile(pamt_path):
        return []

    pamt = parse_pamt(pamt_path, paz_dir=dir_0008)

    # Check for new staticinfo format first
    hdr_entry = None
    body_entry = None
    ii_entry = None
    for e in pamt:
        lower = e.path.lower()
        if "iteminfo.staticinfoheader" in lower:
            hdr_entry = e
        elif "iteminfo.staticinfobody" in lower:
            body_entry = e
        elif "iteminfo.pabgb" in lower:
            ii_entry = e

    if hdr_entry and body_entry:
        return _parse_iteminfo_static(hdr_entry, body_entry, loc_dict)
    elif ii_entry:
        return _parse_iteminfo_legacy(ii_entry, loc_dict)
    return []


def _parse_iteminfo_static(hdr_entry, body_entry, loc_dict: dict[str, str]) -> list[ItemRecord]:
    with open(hdr_entry.paz_file, 'rb') as f:
        f.seek(hdr_entry.offset)
        raw_h = f.read(hdr_entry.comp_size if hdr_entry.compressed else hdr_entry.orig_size)
    hdr = lz4.block.decompress(raw_h, hdr_entry.orig_size) if hdr_entry.compressed else raw_h

    with open(body_entry.paz_file, 'rb') as f:
        f.seek(body_entry.offset)
        raw_b = f.read(body_entry.comp_size if body_entry.compressed else body_entry.orig_size)
    body = lz4.block.decompress(raw_b, body_entry.orig_size) if body_entry.compressed else raw_b

    count = struct.unpack_from('<H', hdr, 0)[0]
    items = []
    seen_ids = set()

    for i in range(count):
        item_id, off = struct.unpack_from('<II', hdr, 2 + i * 8)
        if item_id in seen_ids or off >= len(body):
            continue
        seen_ids.add(item_id)

        next_off = struct.unpack_from('<II', hdr, 2 + (i + 1) * 8)[1] if i + 1 < count else len(body)
        next_off = min(next_off, len(body))

        nlen = struct.unpack_from('<I', body, off)[0]
        if nlen <= 0 or off + 4 + nlen > len(body):
            continue
        name = body[off + 4:off + 4 + nlen].decode('ascii', errors='replace')

        loc_key = str((item_id << 32) | 0x70)
        display_name = loc_dict.get(loc_key, "")
        if not display_name:
            display_name = name.replace('_', ' ')

        item_chunk = body[off:next_off]
        prefab_hashes = []
        for p in range(0, len(item_chunk) - 3, 4):
            h = struct.unpack_from('<I', item_chunk, p)[0]
            if h != 0 and h != item_id:
                prefab_hashes.append(h)

        items.append(ItemRecord(
            item_id=item_id,
            internal_name=name,
            display_name=display_name,
            prefab_hashes=prefab_hashes,
        ))

    return items


def _parse_iteminfo_legacy(ii_entry, loc_dict: dict[str, str]) -> list[ItemRecord]:
    read_size = ii_entry.comp_size if ii_entry.compressed else ii_entry.orig_size
    with open(ii_entry.paz_file, 'rb') as f:
        f.seek(ii_entry.offset)
        raw = f.read(read_size)

    data = lz4.block.decompress(raw, uncompressed_size=ii_entry.orig_size)
    FULL_MARKER = b'\x00\x01\x00\x00\x00\x00\x00\x00\x00\x07\x70\x00\x00\x00'
    items = []
    seen_ids = set()
    idx = 0

    while True:
        pos = data.find(FULL_MARKER, idx)
        if pos == -1:
            break
        idx = pos + len(FULL_MARKER)

        null_pos = pos
        name_start = null_pos
        while name_start > 0 and data[name_start - 1] >= 0x21 and data[name_start - 1] <= 0x7e:
            name_start -= 1
            if null_pos - name_start > 150:
                break

        if null_pos - name_start < 3 or name_start < 8:
            continue

        name = data[name_start:null_pos].decode('ascii', errors='replace')
        if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', name):
            continue

        name_len = struct.unpack_from('<I', data, name_start - 4)[0]
        item_id = struct.unpack_from('<I', data, name_start - 8)[0]

        if name_len not in (len(name), len(name) + 1) or item_id < 100 or item_id > 100000000:
            continue
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        loc_off = pos + 14 + 4
        loc_str = ""
        if loc_off + 4 < len(data):
            loc_len = struct.unpack_from('<I', data, loc_off)[0]
            if 5 < loc_len < 25 and loc_off + 4 + loc_len <= len(data):
                loc_bytes = data[loc_off + 4:loc_off + 4 + loc_len]
                if all(0x30 <= b <= 0x39 for b in loc_bytes):
                    loc_str = loc_bytes.decode('ascii')

        prefab_hashes = []
        search_end = min(len(data), pos + 800)
        for scan in range(pos + 14, search_end - 15):
            if data[scan] == 0x0E:
                count1 = struct.unpack_from('<I', data, scan + 3)[0]
                count2 = struct.unpack_from('<I', data, scan + 7)[0]
                if 0 < count1 <= 5 and 0 < count2 <= 5:
                    for h_idx in range(count2):
                        h = struct.unpack_from('<I', data, scan + 11 + h_idx * 4)[0]
                        if h != 0:
                            prefab_hashes.append(h)
                    if prefab_hashes:
                        break

        display_name = loc_dict.get(loc_str, "") if loc_str else ""

        items.append(ItemRecord(
            item_id=item_id,
            internal_name=name,
            display_name=display_name,
            prefab_hashes=prefab_hashes,
        ))

    return items


# ── Hash table + index builder ────────────────────────────────────

_PAC_PATH_RE = re.compile(rb'character/[a-z_/0-9]+\.pac')


def build_hash_table(pamt_entries: list) -> dict[int, str]:
    """Build hash → prefab/PAC basename lookup from PAMT entries."""
    hash_to_name = {}
    for e in pamt_entries:
        if not (e.path.endswith('.prefab') or e.path.endswith('.pac')):
            continue
        base = os.path.splitext(os.path.basename(e.path))[0]
        for suffix in ('', '_l', '_r', '_u', '_s', '_t',
                       '_index01', '_index02', '_index03'):
            name = base + suffix
            h = hashlittle(name.encode('ascii'), 0xC5EDE)
            hash_to_name[h] = name
    return hash_to_name


def build_prefab_pac_map(pamt_entries: list,
                         progress_fn=None) -> dict[str, list[str]]:
    """Read .prefab binaries from PAZ and extract PAC paths they reference.

    Returns {prefab_basename: [pac_basename, ...]} for all prefabs that
    contain at least one PAC path.  Prefabs are uncompressed/unencrypted,
    so this is a simple raw read + regex scan (~29 MB, <1 s).
    """
    prefab_entries = [e for e in pamt_entries if e.path.endswith('.prefab')]
    if progress_fn:
        progress_fn(f"Reading {len(prefab_entries):,} prefab files...")

    # Group by PAZ file for efficient sequential I/O
    by_paz: dict[str, list] = {}
    for e in prefab_entries:
        by_paz.setdefault(e.paz_file, []).append(e)

    result: dict[str, list[str]] = {}
    for paz_file, entries in by_paz.items():
        with open(paz_file, 'rb') as f:
            for e in entries:
                f.seek(e.offset)
                data = f.read(e.orig_size)
                paths = _PAC_PATH_RE.findall(data)
                if paths:
                    prefab_base = os.path.splitext(os.path.basename(e.path))[0]
                    pac_bases = []
                    for p in paths:
                        name = p.decode('ascii').rsplit('/', 1)[-1][:-4]  # strip dir + .pac
                        if name not in pac_bases:
                            pac_bases.append(name)
                    result[prefab_base] = pac_bases

    if progress_fn:
        progress_fn(f"Prefab map: {len(result):,} prefabs with PAC paths")
    return result


def build_item_index(game_dir: str, pamt_0009_entries: list,
                     progress_fn=None) -> ItemIndex:
    """Build full item index: display name → PAC filenames."""
    loc_dict = parse_localization(game_dir, progress_fn)
    if progress_fn:
        progress_fn(f"Localization: {len(loc_dict):,} strings")

    items = parse_iteminfo(game_dir, loc_dict, progress_fn)
    if progress_fn:
        progress_fn(f"Items: {len(items):,} records")

    hash_table = build_hash_table(pamt_0009_entries)
    if progress_fn:
        progress_fn(f"Hash table: {len(hash_table):,} entries")

    prefab_pac_map = build_prefab_pac_map(pamt_0009_entries, progress_fn)

    # Resolve prefab hashes → PAC filenames
    pac_to_items: dict[str, list[ItemRecord]] = {}
    items_with_models = []

    for item in items:
        if not item.display_name and not item.prefab_hashes:
            continue

        for h in item.prefab_hashes:
            resolved = hash_table.get(h)
            if not resolved:
                continue

            # Try prefab map with full resolved name first (e.g. _u set prefabs)
            pac_bases = prefab_pac_map.get(resolved)

            if not pac_bases:
                # Strip suffixes and retry
                base = resolved
                for sfx in ('_l', '_r', '_u', '_s', '_t',
                            '_index01', '_index02', '_index03'):
                    if base.endswith(sfx):
                        base = base[:-len(sfx)]
                        break
                pac_bases = prefab_pac_map.get(base)

            if pac_bases:
                for pb in pac_bases:
                    pac_name = pb + '.pac'
                    if pac_name not in item.pac_files:
                        item.pac_files.append(pac_name)
                    pac_to_items.setdefault(pac_name, []).append(item)
            else:
                # Direct PAC match (no prefab indirection)
                pac_name = base + '.pac'
                if pac_name not in item.pac_files:
                    item.pac_files.append(pac_name)
                pac_to_items.setdefault(pac_name, []).append(item)

        if item.display_name:
            items_with_models.append(item)

    if progress_fn:
        progress_fn(f"Items with models: {len(items_with_models):,}")

    return ItemIndex(items=items_with_models, pac_to_items=pac_to_items,
                     prefab_pac_map=prefab_pac_map)


# ── Character index (from .app.xml appearance files) ─────────────

def _parse_app_label(app_name: str) -> str:
    """Extract readable label from app.xml filename.

    cd_phm_macduff_00000  → macduff
    cd_m0001_00_bear_00000 → bear
    cd_r0002_00_horse_00003 → horse (variant 3)
    """
    parts = app_name.split('_')
    # Strip leading cd + type prefix (cd_phm_, cd_m0001_00_, cd_r0002_00_, etc.)
    # Find the first part that is NOT: 'cd', a type code, or a 2-digit segment
    name_parts = []
    skip = True
    for i, p in enumerate(parts):
        if skip:
            if p == 'cd':
                continue
            if re.match(r'^(phm|phw|pom|pgm|pgw|ptm|pdm|pdw|ppdm|pow)$', p):
                continue
            if re.match(r'^[mrt]\d{4}$', p):
                continue
            if re.match(r'^\d{2}$', p) and i < 4:
                continue
            skip = False
        if not skip:
            name_parts.append(p)

    if not name_parts:
        return app_name

    # Last part is usually the variant number (00000, 00001, etc.)
    variant = name_parts[-1] if re.match(r'^\d{5}$', name_parts[-1]) else None
    core = '_'.join(name_parts[:-1]) if variant else '_'.join(name_parts)

    if not core:
        return app_name

    if variant and variant != '00000':
        return f"{core} (variant {int(variant)})"
    return core


def build_character_index(pamt_entries: list, prefab_pac_map: dict,
                          progress_fn=None) -> CharacterIndex:
    """Parse .app.xml files and resolve prefab names to PAC files.

    Returns CharacterIndex with all appearance entries that have at least
    one resolved PAC model file.
    """
    import xml.etree.ElementTree as ET

    if progress_fn:
        progress_fn("Loading character appearances...")

    app_entries = [e for e in pamt_entries
                   if e.path.endswith('.app.xml')]

    if not app_entries:
        return CharacterIndex(characters=[], pac_to_chars={})

    # Group by PAZ file for sequential I/O
    by_paz: dict[str, list] = {}
    for e in app_entries:
        by_paz.setdefault(e.paz_file, []).append(e)

    characters = []
    pac_to_chars: dict[str, list[CharacterRecord]] = {}

    for paz_file, paz_entries in by_paz.items():
        with open(paz_file, 'rb') as f:
            for e in paz_entries:
                f.seek(e.offset)
                raw = f.read(e.orig_size)
                xml_data = decrypt(raw, e.path)
                try:
                    root = ET.fromstring(xml_data)
                except Exception:
                    continue

                app_name = os.path.splitext(os.path.basename(e.path))[0]
                # Remove .app suffix if present (filename is X.app.xml)
                if app_name.endswith('.app'):
                    app_name = app_name[:-4]

                pac_files = []
                for prefab_el in root.findall('.//Prefab'):
                    name = prefab_el.get('Name', '')
                    if not name:
                        continue
                    pac_bases = prefab_pac_map.get(name.lower())
                    if pac_bases:
                        for pb in pac_bases:
                            pac_name = pb + '.pac'
                            if pac_name not in pac_files:
                                pac_files.append(pac_name)

                if not pac_files:
                    continue

                label = _parse_app_label(app_name)

                # Extract display name from sound/voice event
                extra_names = []
                for se in root.findall('.//SoundEvent'):
                    vname = se.get('Name', '')
                    if vname.startswith('vce_pc_'):
                        extra_names.append(vname[7:])  # strip "vce_pc_"

                if label.lower() == 'damian' or 'damian' in app_name.lower():
                    label = "Damiane"
                    extra_names.extend(["damiane", "damian", "phw"])

                search_key = f"{app_name} {label} {' '.join(extra_names)}".lower()

                rec = CharacterRecord(
                    app_name=app_name,
                    display_label=label,
                    search_key=search_key,
                    pac_files=pac_files,
                )
                characters.append(rec)
                for pac_name in pac_files:
                    pac_to_chars.setdefault(pac_name, []).append(rec)

    characters.sort(key=lambda c: c.app_name)

    if progress_fn:
        progress_fn(f"Characters: {len(characters):,} appearances, "
                    f"{len(pac_to_chars):,} PAC files")

    return CharacterIndex(characters=characters, pac_to_chars=pac_to_chars)
