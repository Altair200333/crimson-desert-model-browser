# Crimson Desert Archive Format & Update Guide

This document describes the binary architecture of Crimson Desert's asset archives (`.pamt` index files and `.paz` data containers), how the update/DLC breaking changes were diagnosed and resolved, and a step-by-step guide for future AI agents or developers to quickly adapt to future game updates.

---

## 1. Architecture Overview

Crimson Desert (running on the BlackSpace Engine) organizes game assets into numbered categories:
- `0000`: Static objects / meshes (`.pam`, `.hkx`)
- `0007`: Effects (`.pam`)
- `0008`: Static game database (`iteminfo`, gimmick charts)
- `0009`: Characters & skinned meshes (`.pac`, `.pab`, `.prefab`, `.app.xml`)
- `0015`: Terrain & environments (`.pam`)
- `0020`: Localization string tables (`.paloc`)
- `dlc/dlc_XX/`: DLC packages containing the same numbered directory structure (`0000` through `0040`).

Within each directory:
- `0.pamt`: Binary master index file mapping virtual paths to byte offsets, sizes, flags, and PAZ containers.
- `0.paz`, `1.paz`, `...`: Container chunks storing raw or compressed/encrypted assets.

---

## 2. Breaking Changes & Root Cause Analysis

### The Error
```text
unpack_from requires a buffer of at least 9004692 bytes for unpacking 4 bytes at offset 9004688 (actual buffer size is 9004586)
```

### The Root Cause
1. **Radix Trie with LEB128 Varints (Previously assumed fixed `<I` 4-byte parents):**
   - The original parser in `lazorr410-unpacker/python/paz_parse.py` read hierarchy nodes and folder entries as fixed 4-byte parent references: `struct.unpack_from('<I', data, off)[0]`.
   - In reality, both the **Folder Section** and the **Node Section** use a compressed radix trie. Each entry begins with a **variable-length integer (LEB128 / Varint)** representing a relative backward offset (`delta = rel - parent_rel`), followed by a 1-byte string length (`slen`) and the ASCII/UTF-8 path fragment.
   - When the parser read 4 fixed bytes instead of a variable-length byte sequence, it read characters from strings as parent IDs and string lengths. This threw the read pointer out of sync, causing the parser to jump past section boundaries and overshoot the file buffer.

2. **Tail Extension Table:**
   - In modern archives, file extensions are stripped from the node tree and stored in an extension table at the very end of the `.pamt` file (`.dds`, `.pac`, `.pam`, `.app_xml`, etc.).
   - The file record links to an extension via `(flags >> 24) & 0xFF`.

3. **Compound Extensions & ChaCha20 Seed Derivation:**
   - Compound XML extensions in the game archive are stored with underscores (e.g. `.app_xml`, `.pac_xml`, `.prefabdata_xml`).
   - ChaCha20 decryption key derivation (`hashlittle(basename.lower(), 0xC5EDE)`) hashes the **exact filename stored in the archive**. Normalizing the extension to `.app.xml` for browser consumption required updating `derive_key_iv()` to preserve the `.app_xml` hash input.

4. **Static Info Database & Modular Localization:**
   - The update replaced monolithic `localizationstring_eng` with modular `.paloc` files in `0020/` (`item.paloc`, `character.paloc`, etc.). Localization IDs are computed as `str((item_id << 32) | 0x70)`.
   - The item database in `0008/` transitioned from `iteminfo.pabgb` to two companion files: `iteminfo.staticinfoheader` (2-byte count, then 8-byte entries of `[item_id: 4B][offset: 4B]`) and `iteminfo.staticinfobody` (item records containing internal name, loc keys, and 3D model prefab hashes).

5. **DLC Directory Hierarchy:**
   - DLC content resides under `dlc/dlc_01/` (and potential future `dlc_XX/` folders) with identical numbered archive structures. The model browser now scans both the base game directories and all installed DLC folders.

---

## 3. PAMT Binary Specification

Each `.pamt` index file has the following binary layout:

### A. Header (16 bytes)
| Offset | Type | Description |
|---|---|---|
| `0x00` | `uint32` | Magic / version identifier (varies by archive, e.g. `0x72f716e4`) |
| `0x04` | `uint32` | `paz_count`: Number of PAZ container files in this archive |
| `0x08` | `uint32` | Hash |
| `0x0C` | `uint32` | Zero / padding |

### B. PAZ Container Table
`paz_count` entries describing container sizes:
```python
for i in range(paz_count):
    hash_val = unpack('<I')  # 4 bytes
    paz_size = unpack('<I')  # 4 bytes
    if i < paz_count - 1:
        sep = unpack('<I')   # 4 bytes separator
```
Container filenames map to `<pamt_stem + paz_index>.paz` (e.g. `0.paz`, `1.paz`, ...).

### C. Folder Section (Radix Trie)
| Field | Type | Description |
|---|---|---|
| `folder_size` | `uint32` | Size of the folder table payload in bytes |
| `folder_bytes` | `bytes` | `folder_size` bytes of compressed radix trie entries |

Each entry in `folder_bytes`:
- `delta` (`LEB128 varint`): Backward byte offset to parent folder (`parent_rel = rel - delta if delta != 0 else None`).
- `slen` (`uint8`): Length of name fragment.
- `name` (`bytes[slen]`): UTF-8/ASCII folder fragment (e.g. `character/`, `appearance/`, `1_pc/`).

### D. Node Section (Path Tree)
| Field | Type | Description |
|---|---|---|
| `node_size` | `uint32` | Size of the node table payload in bytes |
| `node_bytes` | `bytes` | `node_size` bytes of compressed radix trie entries |

Each entry in `node_bytes`:
- `delta` (`LEB128 varint`): Backward byte offset to parent node (`parent_rel = rel - delta if delta != 0 else None`).
- `slen` (`uint8`): Length of filename fragment.
- `name` (`bytes[slen]`): UTF-8/ASCII node fragment (e.g. `cd_phm_dwayne_00000`).

### E. Record Section
| Field | Type | Description |
|---|---|---|
| `folder_count` | `uint32` | Number of folder records |
| `hash` | `uint32` | Hash |
| `folder_records` | `folder_count * 16 bytes` | Array of 16-byte folder descriptors |

Each folder record:
```python
folder_rel_offset, file_start_index, file_count, folder_hash = unpack('<IIII', data, off)
```
- `folder_rel_offset`: Offset into `folder_bytes` resolving the directory path.
- `file_start_index`: Index of first file record belonging to this folder.
- `file_count`: Number of file records in this folder.

### F. File Records
Total files = `sum(file_count for all folder_records)`.
Each file record is 20 bytes:
```python
node_ref, paz_offset, comp_size, orig_size, flags = unpack('<IIIII', data, off)
```
- `node_ref`: Byte offset into `node_bytes` resolving the filename stem.
- `paz_offset`: Byte offset within the container `.paz` file.
- `comp_size`: Compressed size in bytes.
- `orig_size`: Original uncompressed size in bytes.
- `flags`: Bitfield:
  - `flags & 0xFF`: PAZ file index (added to pamt stem for `.paz` filename).
  - `(flags >> 16) & 0x0F`: Compression type (`0`: none, `2`: LZ4 block).
  - `(flags >> 24) & 0xFF`: Index into Extension Table.

### G. Extension Table (if present at tail)
| Field | Type | Description |
|---|---|---|
| `ext_size` | `uint32` | Length of extension string table in bytes |
| `ext_bytes` | `bytes` | Null-terminated extension strings (e.g. `\x00.pab\x00.pac\x00.app_xml\x00...`) |

---

## 4. Modified Code Locations

1. **[`lazorr410-unpacker/python/paz_parse.py`](file:///C:/Users/kubak/Desktop/Repositories/crimson-desert-model-browser/lazorr410-unpacker/python/paz_parse.py):**
   - Added `_read_varint()` helper.
   - Replaced fixed-width folder/node loops with LEB128 relative backward delta resolution.
   - Added folder record and extension table parsing.
   - Normalized `_xml` extensions to `.xml`.

2. **[`lazorr410-unpacker/python/paz_crypto.py`](file:///C:/Users/kubak/Desktop/Repositories/crimson-desert-model-browser/lazorr410-unpacker/python/paz_crypto.py):**
   - Updated `derive_key_iv()` to translate normalized `.app.xml`, `.pac.xml`, `.paccd.xml`, and `.prefabdata.xml` back to `.app_xml`, etc. so the ChaCha20 seed matches the archive's internal naming.

3. **[`src/browser/catalog.py`](file:///C:/Users/kubak/Desktop/Repositories/crimson-desert-model-browser/src/browser/catalog.py):**
   - Updated `build_catalog()` to discover and iterate through all `dlc/<dlc_name>/` packages alongside base game archives.

4. **[`src/item_db.py`](file:///C:/Users/kubak/Desktop/Repositories/crimson-desert-model-browser/src/item_db.py):**
   - Added `_parse_iteminfo_static()` to read `iteminfo.staticinfoheader` and `iteminfo.staticinfobody` in `0008/`.
   - Updated `parse_localization()` to parse `.paloc` files in `0020/` and map IDs using `(item_id << 32) | 0x70`.
   - Preserved legacy fallback methods (`_parse_iteminfo_legacy()`).

---

## 5. Future Agent / Developer Playbook

If a future update or DLC arrives and breaks loading:

### Step 1: Run diagnostic check across all archives
Run the following check to see which specific `.pamt` file fails:
```python
import glob
from paz_parse import parse_pamt

game_dir = "G:/SteamLibrary/steamapps/common/Crimson Desert"
all_pamts = glob.glob(f"{game_dir}/*/0.pamt") + glob.glob(f"{game_dir}/dlc/*/*/0.pamt")

for p in all_pamts:
    try:
        entries = parse_pamt(p)
        print(f"OK: {p} ({len(entries):,} entries)")
    except Exception as e:
        print(f"FAILED: {p} -> {e}")
```

### Step 2: Check boundary sizes
If an offset buffer error occurs, print the file size and section boundaries:
```python
import struct

with open(failing_pamt, "rb") as f:
    data = f.read()

magic, paz_count = struct.unpack_from("<II", data, 0)
off = 16 + paz_count * 8 + (paz_count - 1) * 4
folder_size = struct.unpack_from("<I", data, off)[0]; off += 4 + folder_size
node_size = struct.unpack_from("<I", data, off)[0]; off += 4 + node_size
folder_count = struct.unpack_from("<I", data, off)[0]; off += 8 + folder_count * 16

print(f"Total: {len(data)}, off after folder records: {off}")
```

### Step 3: Check for new DLC folders
Check if new subdirectories exist under `dlc/` (e.g. `dlc_02`, `dlc_03`):
```python
import os
print(os.listdir(os.path.join(game_dir, "dlc")))
```
`build_catalog` in `src/browser/catalog.py` automatically scans any subdirectories found in `dlc/`, so simply placing new DLC directories there will index them automatically.

