#pragma once

// C API for P/Invoke from C# GUI.
// Build target: paz-native (shared library / DLL).

#ifdef _WIN32
#define PAZ_EXPORT __declspec(dllexport)
#else
#define PAZ_EXPORT __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/// Decrypt a PAZ entry using a filename-derived ChaCha20 key.
/// Writes decrypted data to `out` (must be at least `len` bytes).
/// The filename is used to derive the key (basename, lowercased).
PAZ_EXPORT void paz_decrypt(const char *filename,
                            const unsigned char *ciphertext,
                            unsigned int len,
                            unsigned char *out);

/// LZ4 block decompress.
/// Writes decompressed data to `out` (must be at least `dstSize` bytes).
/// Returns actual decompressed size, or 0 on failure.
PAZ_EXPORT unsigned int paz_lz4_decompress(const unsigned char *src,
                                           unsigned int srcLen,
                                           unsigned char *dst,
                                           unsigned int dstSize);

#ifdef __cplusplus
}
#endif
