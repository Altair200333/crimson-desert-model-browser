# Crimson Desert PAZ Entry Decryption

## Cipher

**ChaCha20** (OpenSSL NID 1019)
- Key: 32 bytes, unique per file entry
- IV/Nonce: 16 bytes, unique per file entry
- Stream cipher — no padding, output size = input size
- Decryption is identical to encryption (XOR-based stream)

## Key Database

~~`paz_keydb.json` contains 1514 unique key/IV pairs captured from a live game session.~~

**Keys are now fully deterministic** — derived from the filename alone. The key database is no longer needed for decryption. See "Key Derivation" below.

The legacy `paz_keydb.json` is retained for reference/validation only.

## Decryption Algorithm

```python
from paz_crypto import derive_key_iv, decrypt_entry

key, iv = derive_key_iv("rendererconfiguration.xml")
plaintext = decrypt_entry(ciphertext, key, iv)
```

## Key Derivation

The entire 32-byte key + 16-byte IV is derived from the **filename alone** (basename, lowercase, no directory prefix).

### Step 1: Seed

```
seed = hashlittle(basename.lower().encode('utf-8'), initval=0xC5EDE)
```

`hashlittle` is Bob Jenkins' lookup3 hash (returns the primary `c` value).

### Step 2: IV

The seed bytes (little-endian uint32) repeated 4 times:

```
iv = pack('<I', seed) * 4     # 16 bytes
```

### Step 3: Key

XOR the seed with `0x60616263`, then XOR with 8 per-chunk deltas:

```
key_base = seed ^ 0x60616263
deltas = [0x00000000, 0x0A0A0A0A, 0x0C0C0C0C, 0x06060606,
          0x0E0E0E0E, 0x0A0A0A0A, 0x06060606, 0x02020202]
key = concat(pack('<I', key_base ^ d) for d in deltas)   # 32 bytes
```

### Example

`rendererconfigurationmaterial.xml` → seed `0xaf3dcef3`:
- IV: `f3ce3daf f3ce3daf f3ce3daf f3ce3daf`
- Key base: `0xaf3dcef3 ^ 0x60616263 = 0xcf5cac90`
- Key: `90ac5ccf 9aa656c5 9ca050c3 96aa5ac9 9ea252c1 9aa656c5 96aa5ac9 92ae5ecd`

## Validation for XML Files

Decrypted XML entries typically start with:
- UTF-8 BOM (`EF BB BF`) followed by `<TagName>`
- Or directly `<?xml version=...`

Check for a valid XML opening tag within the first 8 bytes to confirm correct decryption.

## Compression (LZ4)

Many PAZ entries are LZ4-compressed (block format, no frame header). The PAMT metadata tracks both `comp_size` and `orig_size` — when they differ, the entry is compressed.

The decrypted content IS the LZ4 stream directly. Decompress with:

```python
from paz_decompress import decompress
plaintext = decompress(decrypted_bytes, orig_size)
```

Compression types (from PAMT flags):
- 0: no compression
- 2: LZ4 block (most common)
- 3: custom engine compression
- 4: zlib

## PAMT Metadata

`.pamt` files contain the PAZ archive index: file paths, offsets, sizes, and compression info. Parse with:

```python
from parse_paz import parse_pamt
entries = parse_pamt("0.pamt", paz_dir="/path/to/0003")
for e in entries:
    print(e.path, e.comp_size, e.orig_size, e.compressed)
```

## Files

| File | Purpose |
|------|---------|
| `paz_crypto.py` | Key derivation + ChaCha20 encrypt/decrypt |
| `paz_decompress.py` | LZ4 block decompression |
| `paz_repack.py` | Repack modified assets: compress → encrypt → patch PAZ |
| `parse_paz.py` | PAMT metadata parser |
| `paz_keydb.json` | Legacy captured key/IV database (retained for validation) |

## Repacking Modified Assets

Pipeline: modified plaintext → LZ4 compress → ChaCha20 encrypt → patch PAZ archive.

Since ChaCha20 is a XOR-based stream cipher, encryption is identical to decryption — same key, same IV, same function.

### Constraints

The game reads exactly `comp_size` bytes from the PAZ at the recorded offset, and the PAMT index is integrity-checked at load. This means:

- The encrypted blob must be exactly `comp_size` bytes (the original encrypted size).
- The decompressed output must be exactly `orig_size` bytes.
- The PAMT file must not be modified.
- NTFS timestamps on the `.paz` file must be preserved (the game validates `CreationTime`).

### Two Modes

**Standalone repack** — writes a new encrypted file (for testing before committing to the archive):

```
python paz_repack.py <modified.xml> <original_encrypted.xml> <output.bin> [--keydb paz_keydb.json]
```

This compresses the modified file with LZ4, zero-pads to the original encrypted size, encrypts with the same key/IV, and writes the result. Useful for verifying the round-trip before touching the PAZ.

**In-place PAZ patching** — writes directly into the archive at the correct offset:

```
python paz_repack.py <modified.xml> <original_encrypted.xml> --paz <archive.paz> --offset 0x1A3F00
```

The offset comes from the PAMT metadata (`PazFileEntry.offset` from `parse_paz.py`).

### Size-Matching for Compressed Entries

When `comp_size != orig_size` (LZ4-compressed entries), the modified file must compress to exactly `comp_size` bytes. The repacker handles this automatically with a multi-phase padding strategy:

1. **Pad to `orig_size`**: Insert dummy XML elements (`<Element Name="Pad0" .../>`) with tunable space runs before closing tags to reach the exact decompressed size.
2. **Tune compressibility down**: Replace spaces in the padding with incompressible byte sequences (printable ASCII `0x21..0x7E`) until `lz4.block.compress()` output hits `comp_size` exactly.
3. **Tune compressibility up** (if over budget): Replace bytes inside XML comments with spaces to increase compressibility.
4. **Fallback**: Append an incompressible XML comment (`<!--...-->`) at the tail, binary-searching the payload length until compressed size matches.

For uncompressed entries (`comp_size == orig_size`), the plaintext is compressed and zero-padded to the budget.

### Timestamp Preservation

The repacker captures all three NTFS timestamps (creation, access, modification) via `kernel32.GetFileTime` before writing, then restores them with `SetFileTime` after the patch. This prevents the game's integrity check from detecting the modification.

### Locating an Entry in the Archive

Use `parse_paz.py` to find the PAZ file, offset, and sizes for a given path:

```python
from parse_paz import parse_pamt

entries = parse_pamt("0.pamt", paz_dir="./0003")
for e in entries:
    if "rendererconfiguration" in e.path.lower():
        print(f"PAZ: {e.paz_file}  offset: 0x{e.offset:08X}")
        print(f"comp_size: {e.comp_size}  orig_size: {e.orig_size}")
        print(f"compressed: {e.compressed}")
```

### Full Round-Trip Example

```python
from paz_crypto import derive_key_iv, decrypt_entry
from paz_decompress import decompress

# 1. Derive key from filename
key, iv = derive_key_iv("rendererconfiguration.xml")

# 2. Decrypt
with open("original_encrypted.bin", "rb") as f:
    ct = f.read()
plain = decrypt_entry(ct, key, iv)

# 3. Decompress (if compressed)
if comp_size != orig_size:
    xml = decompress(plain, orig_size)
else:
    xml = plain

# 4. Edit the XML
modified_xml = xml.replace(b"OldValue", b"NewValue")

# 5a. Standalone test repack
with open("modified.xml", "wb") as f:
    f.write(modified_xml)
result = repack("modified.xml", "original_encrypted.bin", "repacked.bin")

# 5b. Or patch directly into the PAZ archive
result = patch_paz("modified.xml", "original_encrypted.bin",
                   "0003/0.paz", paz_offset=0x1A3F00)
```

## Integration Notes

- Dependencies: `pip install cryptography lz4`
- Keys are deterministic from the filename — no runtime capture needed
- The hash input is the lowercase basename only (e.g. `cave.material`, not `material/dist/cave.material`)
- The IV always has a repeating 4-byte pattern (the raw seed bytes)
- Encrypted PAZ folders: 0003, 0011, 0013, 0016. Folder 0000 is unencrypted (PAR header)
- Text content is CP949 (Korean Windows) or UTF-8 with BOM
