"""Comprehensive tests for all PAZ Python tools."""

import sys
import os
import struct
import tempfile
import shutil
import importlib.util

# Ensure we import from python/ not decrypt_info/
sys.path.insert(0, os.path.dirname(__file__))

from paz_crypto import derive_key_iv, hashlittle, chacha20, decrypt, encrypt, lz4_compress, lz4_decompress
from paz_parse import parse_pamt, PazEntry
from paz_unpack import extract_entry
from paz_repack import repack_entry

# Load original implementation for cross-validation
spec = importlib.util.spec_from_file_location(
    'orig_crypto', os.path.join(os.path.dirname(__file__), '..', 'decrypt_info', 'paz_crypto.py'))
orig_crypto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orig_crypto)

TEST_DIR = os.path.join(os.path.dirname(__file__), '..', 'gui', 'Test', 'Resources')
passed = 0
failed = 0


def test(name):
    global passed, failed
    def decorator(fn):
        global passed, failed
        try:
            fn()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name} — {e}")
            failed += 1
    return decorator


# ── Key Derivation Tests ─────────────────────────────────────────────

print("\n=== Key Derivation ===")

@test("documented test vector (rendererconfigurationmaterial.xml)")
def _():
    key, iv = derive_key_iv('rendererconfigurationmaterial.xml')
    seed = struct.unpack('<I', iv[:4])[0]
    assert seed == 0xaf3dcef3
    assert key == bytes.fromhex('90ac5ccf9aa656c59ca050c396aa5ac99ea252c19aa656c596aa5ac992ae5ecd')
    assert iv == struct.pack('<I', 0xaf3dcef3) * 4

@test("cross-validate 6 filenames with decrypt_info")
def _():
    for name in ['lightpreset.xml', 'engineoptionplatform.xml',
                 'rendererconfiguration.xml', 'cave.material',
                 'a.xml', 'test_with_numbers_123.xml']:
        nk, ni = derive_key_iv(name)
        ok, oi = orig_crypto.derive_key_iv(name)
        assert nk == ok and ni == oi, f'{name} mismatch'

@test("hashlittle cross-validate with decrypt_info (5 inputs)")
def _():
    for data in [b'hello', b'', b'a', b'abcdefghijklmnop', b'x' * 100]:
        assert hashlittle(data, 0xC5EDE) == orig_crypto._hashlittle(data, 0xC5EDE)

@test("path stripping — directory prefix ignored")
def _():
    k1, i1 = derive_key_iv('lightpreset.xml')
    k2, i2 = derive_key_iv('technique/lightpreset.xml')
    k3, i3 = derive_key_iv('C:\\game\\technique\\lightpreset.xml')
    assert k1 == k2 == k3
    assert i1 == i2 == i3

@test("case insensitive — uppercase same as lowercase")
def _():
    k1, i1 = derive_key_iv('LightPreset.XML')
    k2, i2 = derive_key_iv('lightpreset.xml')
    assert k1 == k2 and i1 == i2


# ── Encryption/Decryption Tests ──────────────────────────────────────

print("\n=== Encryption / Decryption ===")

@test("decrypt lightpreset.xml — valid BOM + XML")
def _():
    with open(os.path.join(TEST_DIR, 'lightpreset.xml'), 'rb') as f:
        ct = f.read()
    pt = decrypt(ct, 'lightpreset.xml')
    assert pt[:3] == b'\xef\xbb\xbf', f'No BOM: {pt[:3].hex()}'
    assert b'<LightPreset>' in pt[:50]

@test("decrypt engineoptionplatform.xml — produces output")
def _():
    with open(os.path.join(TEST_DIR, 'engineoptionplatform.xml'), 'rb') as f:
        ct = f.read()
    pt = decrypt(ct, 'engineoptionplatform.xml')
    assert len(pt) == len(ct)

@test("Python decryption matches C++ DLL")
def _():
    import ctypes
    dll_path = os.path.join(os.path.dirname(__file__), '..', 'build', 'Release', 'paz-native.dll')
    if not os.path.exists(dll_path):
        raise RuntimeError("paz-native.dll not found, skipping")
    dll = ctypes.CDLL(dll_path)
    dll.paz_decrypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p]
    dll.paz_decrypt.restype = None

    for name in ['lightpreset.xml', 'engineoptionplatform.xml']:
        with open(os.path.join(TEST_DIR, name), 'rb') as f:
            ct = f.read()
        py_pt = decrypt(ct, name)
        out = ctypes.create_string_buffer(len(ct))
        dll.paz_decrypt(name.encode(), ct, len(ct), out)
        assert out.raw == py_pt, f'{name}: Python != C++'

@test("encrypt/decrypt round-trip (small)")
def _():
    data = b'Hello, ChaCha20 round-trip test!'
    enc = encrypt(data, 'test.xml')
    assert enc != data
    assert decrypt(enc, 'test.xml') == data

@test("encrypt/decrypt round-trip (100KB)")
def _():
    data = os.urandom(100000)
    assert decrypt(encrypt(data, 'big.xml'), 'big.xml') == data

@test("encrypt == decrypt (symmetric)")
def _():
    data = b'symmetric test'
    key, iv = derive_key_iv('sym.xml')
    e1 = chacha20(data, key, iv)
    e2 = chacha20(data, key, iv)
    assert e1 == e2


# ── LZ4 Tests ────────────────────────────────────────────────────────

print("\n=== LZ4 Compression ===")

@test("LZ4 round-trip on decrypted XML")
def _():
    with open(os.path.join(TEST_DIR, 'lightpreset.xml'), 'rb') as f:
        ct = f.read()
    pt = decrypt(ct, 'lightpreset.xml')
    comp = lz4_compress(pt)
    assert len(comp) < len(pt)
    assert lz4_decompress(comp, len(pt)) == pt

@test("LZ4 round-trip on random data")
def _():
    # Random data doesn't compress well but should still round-trip
    data = os.urandom(1000)
    comp = lz4_compress(data)
    assert lz4_decompress(comp, len(data)) == data


# ── Full Pipeline Test ───────────────────────────────────────────────

print("\n=== Full Pipeline ===")

@test("decrypt -> compress -> encrypt -> decrypt -> decompress")
def _():
    with open(os.path.join(TEST_DIR, 'lightpreset.xml'), 'rb') as f:
        ct = f.read()
    pt = decrypt(ct, 'lightpreset.xml')
    comp = lz4_compress(pt)
    enc = encrypt(comp, 'lightpreset.xml')
    dec = decrypt(enc, 'lightpreset.xml')
    decomp = lz4_decompress(dec, len(pt))
    assert decomp == pt


# ── PAMT Parser Tests ────────────────────────────────────────────────

print("\n=== PAMT Parser ===")

@test("PazEntry properties")
def _():
    e = PazEntry(path='test/file.xml', paz_file='0.paz', offset=0,
                 comp_size=100, orig_size=200, flags=0x00020001, paz_index=1)
    assert e.compressed == True
    assert e.compression_type == 2
    assert e.encrypted == True

    e2 = PazEntry(path='test/model.pat', paz_file='0.paz', offset=0,
                  comp_size=100, orig_size=100, flags=0x00000000, paz_index=0)
    assert e2.compressed == False
    assert e2.compression_type == 0
    assert e2.encrypted == False

@test("parse synthetic PAMT")
def _():
    buf = bytearray()
    buf += struct.pack('<I', 0x09F510ED)  # magic
    buf += struct.pack('<I', 1)           # paz_count
    buf += struct.pack('<II', 0, 0)       # hash, zero

    # PAZ table
    buf += struct.pack('<II', 0, 4096)    # hash, size

    # Folder section
    folder = bytearray()
    name = b'testpkg'
    folder += struct.pack('<I', 0xFFFFFFFF)
    folder += struct.pack('B', len(name)) + name
    buf += struct.pack('<I', len(folder)) + folder

    # Node section
    nodes = bytearray()
    n0 = b'data/'
    nodes += struct.pack('<I', 0xFFFFFFFF) + struct.pack('B', len(n0)) + n0
    n1_off = len(nodes)
    n1 = b'test.xml'
    nodes += struct.pack('<I', 0) + struct.pack('B', len(n1)) + n1
    buf += struct.pack('<I', len(nodes)) + nodes

    # Record section
    buf += struct.pack('<II', 1, 0)       # folder_count, hash
    buf += b'\x00' * 16                   # folder record
    buf += struct.pack('<IIIII', n1_off, 256, 100, 200, 0x00020000)

    tmpdir = tempfile.mkdtemp()
    try:
        pamt_path = os.path.join(tmpdir, '0.pamt')
        with open(pamt_path, 'wb') as f:
            f.write(bytes(buf))
        paz_path = os.path.join(tmpdir, '0.paz')
        with open(paz_path, 'wb') as f:
            f.write(b'\x00' * 4096)

        entries = parse_pamt(pamt_path, paz_dir=tmpdir)
        assert len(entries) == 1
        e = entries[0]
        assert 'test.xml' in e.path
        assert e.offset == 256
        assert e.comp_size == 100
        assert e.orig_size == 200
        assert e.compression_type == 2
    finally:
        shutil.rmtree(tmpdir)


# ── Unpack Tests ─────────────────────────────────────────────────────

print("\n=== Unpacker ===")

@test("extract_entry — decrypt uncompressed XML")
def _():
    with open(os.path.join(TEST_DIR, 'lightpreset.xml'), 'rb') as f:
        ct = f.read()
    pt = decrypt(ct, 'lightpreset.xml')

    tmpdir = tempfile.mkdtemp()
    try:
        paz_path = os.path.join(tmpdir, '0.paz')
        with open(paz_path, 'wb') as f:
            f.write(ct)

        entry = PazEntry(path='technique/lightpreset.xml', paz_file=paz_path,
                         offset=0, comp_size=len(ct), orig_size=len(ct),
                         flags=0, paz_index=0)

        out_dir = os.path.join(tmpdir, 'out')
        result = extract_entry(entry, out_dir, decrypt_xml=True)
        assert result['decrypted'] == True
        assert result['decompressed'] == False

        extracted = os.path.join(out_dir, 'technique', 'lightpreset.xml')
        with open(extracted, 'rb') as f:
            assert f.read() == pt
    finally:
        shutil.rmtree(tmpdir)

@test("extract_entry — decrypt + decompress LZ4")
def _():
    with open(os.path.join(TEST_DIR, 'lightpreset.xml'), 'rb') as f:
        ct = f.read()
    pt = decrypt(ct, 'lightpreset.xml')

    # Simulate a compressed+encrypted entry
    compressed = lz4_compress(pt)
    encrypted = encrypt(compressed, 'lightpreset.xml')

    tmpdir = tempfile.mkdtemp()
    try:
        paz_path = os.path.join(tmpdir, '0.paz')
        with open(paz_path, 'wb') as f:
            f.write(encrypted)

        entry = PazEntry(path='technique/lightpreset.xml', paz_file=paz_path,
                         offset=0, comp_size=len(encrypted), orig_size=len(pt),
                         flags=0x00020000, paz_index=0)

        out_dir = os.path.join(tmpdir, 'out')
        result = extract_entry(entry, out_dir, decrypt_xml=True)
        assert result['decrypted'] == True
        assert result['decompressed'] == True

        extracted = os.path.join(out_dir, 'technique', 'lightpreset.xml')
        with open(extracted, 'rb') as f:
            assert f.read() == pt
    finally:
        shutil.rmtree(tmpdir)

@test("extract_entry — no decrypt for non-XML")
def _():
    raw_data = os.urandom(256)
    tmpdir = tempfile.mkdtemp()
    try:
        paz_path = os.path.join(tmpdir, '0.paz')
        with open(paz_path, 'wb') as f:
            f.write(raw_data)

        entry = PazEntry(path='models/tree.pat', paz_file=paz_path,
                         offset=0, comp_size=256, orig_size=256,
                         flags=0, paz_index=0)

        out_dir = os.path.join(tmpdir, 'out')
        result = extract_entry(entry, out_dir, decrypt_xml=True)
        assert result['decrypted'] == False
        assert result['decompressed'] == False

        extracted = os.path.join(out_dir, 'models', 'tree.pat')
        with open(extracted, 'rb') as f:
            assert f.read() == raw_data
    finally:
        shutil.rmtree(tmpdir)


# ── Repack Tests ─────────────────────────────────────────────────────

print("\n=== Repacker ===")

@test("repack uncompressed XML — standalone file")
def _():
    with open(os.path.join(TEST_DIR, 'lightpreset.xml'), 'rb') as f:
        ct = f.read()
    pt = decrypt(ct, 'lightpreset.xml')

    tmpdir = tempfile.mkdtemp()
    try:
        modified = os.path.join(tmpdir, 'modified.xml')
        with open(modified, 'wb') as f:
            f.write(pt)

        paz_path = os.path.join(tmpdir, '0.paz')
        with open(paz_path, 'wb') as f:
            f.write(ct)

        entry = PazEntry(path='technique/lightpreset.xml', paz_file=paz_path,
                         offset=0, comp_size=len(ct), orig_size=len(ct),
                         flags=0, paz_index=0)

        output = os.path.join(tmpdir, 'repacked.bin')
        result = repack_entry(modified, entry, output_path=output)
        assert result['action'] == 'written'

        with open(output, 'rb') as f:
            repacked = f.read()
        assert len(repacked) == len(ct)
        assert decrypt(repacked, 'lightpreset.xml')[:len(pt)] == pt
    finally:
        shutil.rmtree(tmpdir)

@test("repack uncompressed XML — in-place PAZ patch")
def _():
    with open(os.path.join(TEST_DIR, 'lightpreset.xml'), 'rb') as f:
        ct = f.read()
    pt = decrypt(ct, 'lightpreset.xml')

    tmpdir = tempfile.mkdtemp()
    try:
        modified = os.path.join(tmpdir, 'modified.xml')
        with open(modified, 'wb') as f:
            f.write(pt)

        # PAZ with some padding before and after
        paz_path = os.path.join(tmpdir, '0.paz')
        with open(paz_path, 'wb') as f:
            f.write(b'\x00' * 512)  # padding before
            f.write(ct)             # entry at offset 512
            f.write(b'\x00' * 512)  # padding after

        entry = PazEntry(path='technique/lightpreset.xml', paz_file=paz_path,
                         offset=512, comp_size=len(ct), orig_size=len(ct),
                         flags=0, paz_index=0)

        result = repack_entry(modified, entry)
        assert result['action'] == 'patched'

        with open(paz_path, 'rb') as f:
            f.seek(512)
            patched = f.read(len(ct))
        assert decrypt(patched, 'lightpreset.xml')[:len(pt)] == pt
    finally:
        shutil.rmtree(tmpdir)

@test("repack dry run — no files modified")
def _():
    with open(os.path.join(TEST_DIR, 'lightpreset.xml'), 'rb') as f:
        ct = f.read()
    pt = decrypt(ct, 'lightpreset.xml')

    tmpdir = tempfile.mkdtemp()
    try:
        modified = os.path.join(tmpdir, 'modified.xml')
        with open(modified, 'wb') as f:
            f.write(pt)

        paz_path = os.path.join(tmpdir, '0.paz')
        with open(paz_path, 'wb') as f:
            f.write(ct)

        entry = PazEntry(path='technique/lightpreset.xml', paz_file=paz_path,
                         offset=0, comp_size=len(ct), orig_size=len(ct),
                         flags=0, paz_index=0)

        result = repack_entry(modified, entry, dry_run=True)
        assert result['action'] == 'dry_run'

        # PAZ should be unchanged
        with open(paz_path, 'rb') as f:
            assert f.read() == ct
    finally:
        shutil.rmtree(tmpdir)

@test("repack rejects oversized file")
def _():
    tmpdir = tempfile.mkdtemp()
    try:
        modified = os.path.join(tmpdir, 'big.xml')
        with open(modified, 'wb') as f:
            f.write(b'x' * 5000)

        paz_path = os.path.join(tmpdir, '0.paz')
        with open(paz_path, 'wb') as f:
            f.write(b'\x00' * 1000)

        entry = PazEntry(path='test.xml', paz_file=paz_path,
                         offset=0, comp_size=1000, orig_size=1000,
                         flags=0, paz_index=0)

        try:
            repack_entry(modified, entry)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass  # expected
    finally:
        shutil.rmtree(tmpdir)


# ── Full Round-Trip: Unpack -> Modify -> Repack -> Unpack ────────────

print("\n=== Full Round-Trip ===")

@test("unpack -> modify -> repack -> unpack (uncompressed)")
def _():
    with open(os.path.join(TEST_DIR, 'lightpreset.xml'), 'rb') as f:
        original_ct = f.read()
    original_pt = decrypt(original_ct, 'lightpreset.xml')

    tmpdir = tempfile.mkdtemp()
    try:
        # Create PAZ with original encrypted data
        paz_path = os.path.join(tmpdir, '0.paz')
        with open(paz_path, 'wb') as f:
            f.write(original_ct)

        entry = PazEntry(path='technique/lightpreset.xml', paz_file=paz_path,
                         offset=0, comp_size=len(original_ct), orig_size=len(original_ct),
                         flags=0, paz_index=0)

        # Step 1: Unpack
        out1 = os.path.join(tmpdir, 'unpacked')
        extract_entry(entry, out1, decrypt_xml=True)
        with open(os.path.join(out1, 'technique', 'lightpreset.xml'), 'rb') as f:
            unpacked = f.read()
        assert unpacked == original_pt

        # Step 2: Modify
        modified = unpacked.replace(b'Sun', b'Mun')
        assert modified != unpacked, "Modification should change content"
        modified_path = os.path.join(tmpdir, 'modified.xml')
        with open(modified_path, 'wb') as f:
            f.write(modified)

        # Step 3: Repack (in-place)
        repack_entry(modified_path, entry)

        # Step 4: Unpack again
        out2 = os.path.join(tmpdir, 'unpacked2')
        extract_entry(entry, out2, decrypt_xml=True)
        with open(os.path.join(out2, 'technique', 'lightpreset.xml'), 'rb') as f:
            re_unpacked = f.read()

        # The re-unpacked content should contain our modification
        assert b'Mun' in re_unpacked
        assert re_unpacked[:len(modified)] == modified
    finally:
        shutil.rmtree(tmpdir)


# ── Summary ──────────────────────────────────────────────────────────

print(f"\n{'=' * 60}")
print(f"Results: {passed} passed, {failed} failed")
print(f"{'=' * 60}")
sys.exit(1 if failed else 0)
