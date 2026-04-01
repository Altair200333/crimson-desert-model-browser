"""Item database: maps in-game item names to model (PAC) files.

Parses the localization DB and iteminfo.pabgb to build a lookup from
English display names → internal names → prefab hashes → PAC filenames.

Usage:
    from item_db import build_item_index, ItemRecord
    index = build_item_index(game_dir, pamt_0009_entries, progress_fn)
    # index.items: list of ItemRecord with display names + PAC filenames
    # index.pac_to_items: dict mapping pac_basename → [ItemRecord, ...]
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
    loc_entry = None
    for e in entries:
        if "localizationstring_eng" in e.path.lower():
            loc_entry = e
            break
    if not loc_entry:
        return {}

    read_size = loc_entry.comp_size if loc_entry.compressed else loc_entry.orig_size
    with open(loc_entry.paz_file, 'rb') as f:
        f.seek(loc_entry.offset)
        raw = f.read(read_size)

    dec = decrypt(raw, loc_entry.path)
    data = lz4.block.decompress(dec, uncompressed_size=loc_entry.orig_size)

    # Binary format: length-prefixed strings in pairs (id, text), separated by
    # variable-length gaps. Scan for numeric ID strings and capture following text.
    loc_dict = {}

    # Find all [u32 len][digit string] patterns — these are loc IDs
    pos = 0
    while pos + 8 < len(data):
        # Try reading a length-prefixed string
        slen = struct.unpack_from('<I', data, pos)[0]
        if slen == 0 or slen > 50000 or pos + 4 + slen > len(data):
            pos += 1
            continue

        s_bytes = data[pos + 4:pos + 4 + slen]

        # Check if this is a numeric ID (10-20 digit string, all ASCII digits)
        if 6 <= slen <= 20 and all(0x30 <= b <= 0x39 for b in s_bytes):
            id_str = s_bytes.decode('ascii')
            # Next should be the text string
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

    return loc_dict


# ── iteminfo.pabgb ───────────────────────────────────────────────

def parse_iteminfo(game_dir: str, loc_dict: dict[str, str],
                   progress_fn=None) -> list[ItemRecord]:
    """Parse iteminfo.pabgb → list of ItemRecord with resolved display names."""
    if progress_fn:
        progress_fn("Loading item database...")

    dir_0008 = os.path.join(game_dir, "0008")
    pamt_path = os.path.join(dir_0008, "0.pamt")
    if not os.path.isfile(pamt_path):
        return []

    pamt = parse_pamt(pamt_path, paz_dir=dir_0008)
    ii_entry = None
    for e in pamt:
        if "iteminfo.pabgb" in e.path:
            ii_entry = e
            break
    if not ii_entry:
        return []

    read_size = ii_entry.comp_size if ii_entry.compressed else ii_entry.orig_size
    with open(ii_entry.paz_file, 'rb') as f:
        f.seek(ii_entry.offset)
        raw = f.read(read_size)

    data = lz4.block.decompress(raw, uncompressed_size=ii_entry.orig_size)

    # Scan using full marker: [name_null][01 00 00 00 00 00 00 00][07 70 00 00 00]
    # Walk back from null byte to get item name + id.
    # After 07 70 00 00 00: [item_id 4B][loc_len 4B][loc_str]
    FULL_MARKER = b'\x00\x01\x00\x00\x00\x00\x00\x00\x00\x07\x70\x00\x00\x00'
    items = []
    seen_ids = set()
    idx = 0

    while True:
        pos = data.find(FULL_MARKER, idx)
        if pos == -1:
            break
        idx = pos + len(FULL_MARKER)

        null_pos = pos  # first byte of marker is the name's null terminator

        # Walk back to find ASCII name
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

        # Read loc_id: marker ends at pos+14, then [item_id 4B][loc_len 4B][loc_str]
        loc_off = pos + 14 + 4  # skip marker + item_id repeat
        loc_str = ""
        if loc_off + 4 < len(data):
            loc_len = struct.unpack_from('<I', data, loc_off)[0]
            if 5 < loc_len < 25 and loc_off + 4 + loc_len <= len(data):
                loc_bytes = data[loc_off + 4:loc_off + 4 + loc_len]
                if all(0x30 <= b <= 0x39 for b in loc_bytes):
                    loc_str = loc_bytes.decode('ascii')

        # Find prefab hashes: search for 0E marker in next 800 bytes
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

    # Resolve prefab hashes → PAC filenames
    pac_to_items: dict[str, list[ItemRecord]] = {}
    items_with_models = []

    for item in items:
        if not item.display_name and not item.prefab_hashes:
            continue

        for h in item.prefab_hashes:
            resolved = hash_table.get(h)
            if resolved:
                # Strip suffixes to get base PAC name
                base = resolved
                for sfx in ('_l', '_r', '_u', '_s', '_t',
                            '_index01', '_index02', '_index03'):
                    if base.endswith(sfx):
                        base = base[:-len(sfx)]
                        break
                pac_name = base + '.pac'
                if pac_name not in item.pac_files:
                    item.pac_files.append(pac_name)
                if pac_name not in pac_to_items:
                    pac_to_items[pac_name] = []
                pac_to_items[pac_name].append(item)

        if item.display_name:
            items_with_models.append(item)

    if progress_fn:
        progress_fn(f"Items with models: {len(items_with_models):,}")

    return ItemIndex(items=items_with_models, pac_to_items=pac_to_items)
