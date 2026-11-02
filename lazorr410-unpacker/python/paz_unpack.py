"""PAZ archive unpacker for Crimson Desert.

Extracts files from PAZ archives, with automatic decryption (ChaCha20)
and decompression (LZ4) based on PAMT metadata.

Usage:
    # Extract everything
    python paz_unpack.py /path/to/0.pamt --paz-dir /path/to/0003 -o output/

    # Extract only XML files
    python paz_unpack.py /path/to/0.pamt --paz-dir /path/to/0003 -o output/ --filter "*.xml"

    # Extract a single file by path
    python paz_unpack.py /path/to/0.pamt --paz-dir /path/to/0003 -o output/ \
        --filter "technique/rendererconfiguration.xml"

    # Dry run (list what would be extracted)
    python paz_unpack.py /path/to/0.pamt --paz-dir /path/to/0003 --dry-run
"""

import os
import sys
import fnmatch
import argparse

from paz_parse import parse_pamt, PazEntry
from paz_crypto import decrypt, lz4_decompress


def extract_entry(entry: PazEntry, output_dir: str, decrypt_xml: bool = True) -> dict:
    """Extract a single entry from a PAZ archive.

    Args:
        entry: parsed PAMT entry
        output_dir: base directory for extracted files
        decrypt_xml: whether to decrypt XML files (default: True)

    Returns:
        dict with extraction info (decrypted, decompressed, size)
    """
    result = {"decrypted": False, "decompressed": False}

    read_size = entry.comp_size if entry.compressed else entry.orig_size

    with open(entry.paz_file, 'rb') as f:
        f.seek(entry.offset)
        data = f.read(read_size)

    # Decrypt encrypted XML files
    if decrypt_xml and entry.encrypted:
        basename = os.path.basename(entry.path)
        data = decrypt(data, basename)
        result["decrypted"] = True

    # Decompress LZ4
    if entry.compressed and entry.compression_type == 2:
        data = lz4_decompress(data, entry.orig_size)
        result["decompressed"] = True

    # Write to disk
    rel_path = entry.path.replace('\\', '/').replace('/', os.sep)
    out_path = os.path.join(output_dir, rel_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, 'wb') as f:
        f.write(data)

    result["size"] = len(data)
    result["path"] = out_path
    return result


def extract_all(entries: list[PazEntry], output_dir: str,
                decrypt_xml: bool = True, verbose: bool = False) -> dict:
    """Extract all entries from PAZ archives.

    Returns:
        dict with summary stats
    """
    total = len(entries)
    decrypted = 0
    decompressed = 0
    errors = 0

    for i, entry in enumerate(entries):
        try:
            result = extract_entry(entry, output_dir, decrypt_xml)
            if result["decrypted"]:
                decrypted += 1
            if result["decompressed"]:
                decompressed += 1
            if verbose:
                flags = []
                if result["decrypted"]: flags.append("decrypted")
                if result["decompressed"]: flags.append("decompressed")
                extra = f" [{', '.join(flags)}]" if flags else ""
                print(f"  [{i+1}/{total}] {entry.path}{extra}")
        except Exception as e:
            errors += 1
            print(f"  ERROR: {entry.path}: {e}", file=sys.stderr)

        if not verbose and (i + 1) % 100 == 0:
            print(f"  {i+1}/{total}...", end='\r')

    if not verbose:
        print()

    return {
        "total": total,
        "decrypted": decrypted,
        "decompressed": decompressed,
        "errors": errors,
    }


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract files from PAZ archives")
    parser.add_argument("pamt", help="Path to .pamt index file")
    parser.add_argument("--paz-dir", help="Directory containing .paz files")
    parser.add_argument("-o", "--output", default="output", help="Output directory (default: output/)")
    parser.add_argument("--filter", help="Filter by glob pattern (e.g. '*.xml')")
    parser.add_argument("--no-decrypt", action="store_true", help="Skip XML decryption")
    parser.add_argument("--dry-run", action="store_true", help="List files without extracting")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show each file as it's extracted")
    args = parser.parse_args()

    print(f"Parsing {args.pamt}...")
    entries = parse_pamt(args.pamt, paz_dir=args.paz_dir)
    print(f"Found {len(entries):,} entries")

    if args.filter:
        pattern = args.filter.lower()
        entries = [e for e in entries
                   if fnmatch.fnmatch(e.path.lower(), pattern)
                   or fnmatch.fnmatch(os.path.basename(e.path).lower(), pattern)
                   or pattern in e.path.lower()]
        print(f"Filtered to {len(entries):,} entries matching '{args.filter}'")

    if not entries:
        print("Nothing to extract.")
        return

    if args.dry_run:
        for e in entries:
            comp = "LZ4" if e.compression_type == 2 else "   "
            enc = "ENC" if e.encrypted else "   "
            print(f"  [{comp}] [{enc}] {e.comp_size:>10,} -> {e.orig_size:>10,}  {e.path}")
        print(f"\n{len(entries):,} entries (dry run)")
        return

    print(f"Extracting to {args.output}/...")
    stats = extract_all(entries, args.output,
                        decrypt_xml=not args.no_decrypt,
                        verbose=args.verbose)

    parts = [f"{stats['total']} extracted"]
    if stats["decrypted"]: parts.append(f"{stats['decrypted']} decrypted")
    if stats["decompressed"]: parts.append(f"{stats['decompressed']} decompressed")
    if stats["errors"]: parts.append(f"{stats['errors']} errors")
    print(f"Done: {', '.join(parts)}")


if __name__ == "__main__":
    main()
