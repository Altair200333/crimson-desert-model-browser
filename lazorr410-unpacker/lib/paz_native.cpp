#include "paz_native.h"
#include "CryptChaCha20.h"
#include "Lz4.h"
#include <cstring>

extern "C" {

void paz_decrypt(const char *filename, const unsigned char *ciphertext,
                 unsigned int len, unsigned char *out) {
    if (!filename || !ciphertext || !out || len == 0) return;
    uint8_t key[32], iv[16];
    paz::deriveKeyIv(std::string(filename), key, iv);
    paz::chacha20(key, iv, ciphertext, out, len);
}

unsigned int paz_lz4_decompress(const unsigned char *src, unsigned int srcLen,
                                unsigned char *dst, unsigned int dstSize) {
    if (!src || !dst || srcLen == 0 || dstSize == 0) return 0;
    auto result = paz::lz4Decompress(src, srcLen, dstSize);
    if (result.empty()) return 0;
    uint32_t outLen = static_cast<uint32_t>(result.size());
    memcpy(dst, result.data(), outLen);
    return outLen;
}

} // extern "C"
