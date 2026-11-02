#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace paz {

/// ChaCha20 encrypt/decrypt (OpenSSL-compatible 16-byte IV).
/// IV layout: bytes 0..3 = initial counter (LE), bytes 4..15 = nonce.
void chacha20(const uint8_t *key, const uint8_t *iv,
              const uint8_t *in, uint8_t *out, uint32_t len);

/// Bob Jenkins' lookup3 hashlittle — returns the primary hash (c).
uint32_t hashlittle(const uint8_t *data, uint32_t length, uint32_t initval);

/// Derive ChaCha20 key (32 bytes) and IV (16 bytes) from a filename.
/// Uses the lowercase basename only.
void deriveKeyIv(const std::string &filename, uint8_t key[32], uint8_t iv[16]);

/// Decrypt a PAZ entry using a filename-derived key.
/// Returns decrypted data.
std::vector<uint8_t> decryptEntry(const uint8_t *ciphertext, uint32_t len,
                                  const std::string &filename);

} // namespace paz
