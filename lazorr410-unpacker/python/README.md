# PAZ Tools (Python)

Python scripts for unpacking and repacking Crimson Desert `.paz` archive files.

## Requirements

```bash
pip install cryptography lz4
```

## Scripts

| Script | Description |
|--------|-------------|
| `paz_parse.py` | Parse `.pamt` index files and list archive contents |
| `paz_unpack.py` | Extract, decrypt, and decompress files from PAZ archives |
| `paz_repack.py` | Repack modified files back into PAZ archives |
| `paz_crypto.py` | Shared library: ChaCha20 key derivation, encryption, LZ4 |

## Quick Start

### List archive contents

```bash
python paz_parse.py /path/to/0.pamt --paz-dir /path/to/0003
```

### Extract all files

```bash
python paz_unpack.py /path/to/0.pamt --paz-dir /path/to/0003 -o output/
```

### Extract specific files

```bash
python paz_unpack.py /path/to/0.pamt --paz-dir /path/to/0003 -o output/ --filter "*.xml"
```

### Repack a modified file

```bash
# Find the entry you want to patch
python paz_parse.py /path/to/0.pamt --paz-dir /path/to/0003 --filter rendererconfiguration

# Patch it into the PAZ archive
python paz_repack.py modified.xml --pamt /path/to/0.pamt --paz-dir /path/to/0003 \
    --entry "technique/rendererconfiguration.xml"
```

## How It Works

PAZ archives store game assets indexed by `.pamt` files. Some entries (XML configs) are encrypted with ChaCha20 and/or compressed with LZ4.

Keys are deterministic — derived from the filename using Bob Jenkins' `hashlittle` hash. No key database needed.

### Encryption

- Cipher: ChaCha20 (symmetric — encrypt and decrypt are the same operation)
- Key: 32 bytes, derived from `hashlittle(basename.lower(), initval=0xC5EDE)`
- IV: 16 bytes (seed repeated 4×)

### Compression

PAMT flags encode the compression type: 0=none, 2=LZ4, 3=custom, 4=zlib.
Only LZ4 is currently supported for decompression/recompression.

### Repacking Constraints

- The encrypted blob must be exactly `comp_size` bytes (original size in PAMT)
- The decompressed output must be exactly `orig_size` bytes
- PAMT files must never be modified (game integrity check)
- NTFS timestamps on `.paz` files must be preserved (game validates CreationTime)

The repacker handles size-matching automatically by padding XML content and tuning compressibility.
