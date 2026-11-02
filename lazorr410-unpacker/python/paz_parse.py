"""PAMT index parser for Crimson Desert PAZ archives.

Parses .pamt files to discover file entries, their locations in PAZ archives,
sizes, and compression info.

Usage:
    python paz_parse.py <file.pamt> [--paz-dir <dir>] [--filter <pattern>]

Library usage:
    from paz_parse import parse_pamt
    entries = parse_pamt("0.pamt", paz_dir="./0003")
    for e in entries:
        print(e.path, e.comp_size, e.orig_size)
"""

import os
import struct
import fnmatch
from dataclasses import dataclass


@dataclass
class PazEntry:
    """A single file entry in a PAZ archive."""
    path: str           # Full path within the archive
    paz_file: str       # Path to the .paz file containing this entry
    offset: int         # Byte offset within the PAZ file
    comp_size: int      # Compressed/stored size in the PAZ
    orig_size: int      # Original decompressed size
    flags: int          # Raw PAMT flags
    paz_index: int      # PAZ file index (from flags & 0xFF)

    @property
    def compressed(self) -> bool:
        return self.comp_size != self.orig_size

    @property
    def compression_type(self) -> int:
        """0=none, 2=LZ4, 3=custom, 4=zlib"""
        return (self.flags >> 16) & 0x0F

    @property
    def encrypted(self) -> bool:
        """XML files in encrypted folders are ChaCha20-encrypted."""
        return self.path.lower().endswith('.xml')


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if (b & 0x80) == 0:
            break
    return result, pos


def parse_pamt(pamt_path: str, paz_dir: str = None) -> list[PazEntry]:
    """Parse a .pamt index file and return all file entries.

    Args:
        pamt_path: path to the .pamt file
        paz_dir: directory containing .paz files (default: same dir as .pamt)

    Returns:
        list of PazEntry
    """
    with open(pamt_path, 'rb') as f:
        data = f.read()

    if paz_dir is None:
        paz_dir = os.path.dirname(pamt_path) or '.'

    pamt_stem = os.path.splitext(os.path.basename(pamt_path))[0]

    off = 0
    off += 4  # skip magic (varies between game versions)

    paz_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += 8  # hash + zero

    # PAZ table
    for i in range(paz_count):
        off += 4  # hash
        off += 4  # size
        if i < paz_count - 1:
            off += 4  # separator

    # Folder section (radix trie with varint relative backward parent offsets)
    folder_size = struct.unpack_from('<I', data, off)[0]; off += 4
    folder_bytes = data[off:off + folder_size]; off += folder_size

    folder_tree = {}
    pos = 0
    while pos < len(folder_bytes):
        rel = pos
        delta, pos = _read_varint(folder_bytes, pos)
        slen = folder_bytes[pos]; pos += 1
        name = folder_bytes[pos:pos + slen].decode('utf-8', errors='replace'); pos += slen
        p_rel = (rel - delta) if delta != 0 else None
        folder_tree[rel] = (p_rel, name)

    # Node section (path tree with varint relative backward parent offsets)
    node_size = struct.unpack_from('<I', data, off)[0]; off += 4
    node_bytes = data[off:off + node_size]; off += node_size

    node_tree = {}
    pos = 0
    while pos < len(node_bytes):
        rel = pos
        delta, pos = _read_varint(node_bytes, pos)
        slen = node_bytes[pos]; pos += 1
        name = node_bytes[pos:pos + slen].decode('utf-8', errors='replace'); pos += slen
        p_rel = (rel - delta) if delta != 0 else None
        node_tree[rel] = (p_rel, name)

    folder_cache = {}
    def build_folder_path(rel):
        if rel in folder_cache:
            return folder_cache[rel]
        parts = []
        cur = rel
        visited = set()
        while cur is not None and cur in folder_tree and cur not in visited:
            visited.add(cur)
            p_rel, n = folder_tree[cur]
            parts.append(n)
            cur = p_rel
        res = "".join(reversed(parts))
        folder_cache[rel] = res
        return res

    node_cache = {}
    def build_node_path(node_ref):
        if node_ref in node_cache:
            return node_cache[node_ref]
        parts = []
        cur = node_ref
        visited = set()
        while cur is not None and cur in node_tree and cur not in visited:
            visited.add(cur)
            p_rel, n = node_tree[cur]
            parts.append(n)
            cur = p_rel
        res = "".join(reversed(parts))
        node_cache[node_ref] = res
        return res

    # Record section
    folder_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += 4  # hash

    folder_recs = []
    for _ in range(folder_count):
        f_off, start_idx, count, _ = struct.unpack_from('<IIII', data, off)
        off += 16
        folder_recs.append((f_off, start_idx, count))

    total_files = sum(c for _, _, c in folder_recs)
    file_recs = []
    for _ in range(total_files):
        node_ref, paz_offset, comp_size, orig_size, flags = \
            struct.unpack_from('<IIIII', data, off)
        off += 20
        file_recs.append((node_ref, paz_offset, comp_size, orig_size, flags))

    # Extension section (if present)
    ext_list = [""]
    if off + 4 <= len(data):
        ext_size = struct.unpack_from('<I', data, off)[0]; off += 4
        ext_bytes = data[off:off + ext_size]
        ext_list = []
        for raw_ext in ext_bytes.split(b'\x00'):
            ext_str = raw_ext.decode('utf-8', errors='replace')
            # Normalize compound extensions with _xml (.app_xml -> .app.xml, .pac_xml -> .pac.xml)
            if ext_str.endswith('_xml'):
                ext_str = ext_str[:-4] + '.xml'
            ext_list.append(ext_str)

    entries = []
    for f_off, start_idx, count in folder_recs:
        folder_path = build_folder_path(f_off)
        for i in range(start_idx, start_idx + count):
            node_ref, paz_offset, comp_size, orig_size, flags = file_recs[i]
            fname = build_node_path(node_ref)
            ext_idx = (flags >> 24) & 0xFF
            ext = ext_list[ext_idx] if ext_idx < len(ext_list) else ""
            full_fname = fname + ext

            if folder_path:
                full_path = f"{folder_path}/{full_fname}" if not folder_path.endswith('/') else f"{folder_path}{full_fname}"
            else:
                full_path = full_fname

            paz_index = flags & 0xFF
            paz_num = int(pamt_stem) + paz_index
            paz_file = os.path.join(paz_dir, f"{paz_num}.paz")

            entries.append(PazEntry(
                path=full_path,
                paz_file=paz_file,
                offset=paz_offset,
                comp_size=comp_size,
                orig_size=orig_size,
                flags=flags,
                paz_index=paz_index,
            ))

    return entries


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Parse PAMT index and list PAZ archive contents")
    parser.add_argument("pamt", help="Path to .pamt file")
    parser.add_argument("--paz-dir", help="Directory containing .paz files (default: same as .pamt)")
    parser.add_argument("--filter", help="Filter entries by glob pattern (e.g. '*.xml', '*renderconfig*')")
    parser.add_argument("--stats", action="store_true", help="Show summary statistics")
    args = parser.parse_args()

    entries = parse_pamt(args.pamt, paz_dir=args.paz_dir)

    if args.filter:
        pattern = args.filter.lower()
        entries = [e for e in entries if fnmatch.fnmatch(e.path.lower(), f"*{pattern}*")
                   or fnmatch.fnmatch(os.path.basename(e.path).lower(), pattern)]

    if args.stats:
        compressed = sum(1 for e in entries if e.compressed)
        encrypted = sum(1 for e in entries if e.encrypted)
        total_comp = sum(e.comp_size for e in entries)
        total_orig = sum(e.orig_size for e in entries)
        print(f"Entries:     {len(entries):,}")
        print(f"Compressed:  {compressed:,}")
        print(f"Encrypted:   {encrypted:,} (XML files)")
        print(f"Total stored: {total_comp:,} bytes ({total_comp / 1024 / 1024:.1f} MB)")
        print(f"Total orig:   {total_orig:,} bytes ({total_orig / 1024 / 1024:.1f} MB)")
        return

    for e in entries:
        comp = "LZ4" if e.compression_type == 2 else "   "
        enc = "ENC" if e.encrypted else "   "
        print(f"[{comp}] [{enc}] {e.comp_size:>10,} -> {e.orig_size:>10,}  "
              f"paz:{e.paz_index} @0x{e.offset:08X}  {e.path}")

    print(f"\n{len(entries):,} entries")


if __name__ == "__main__":
    main()
