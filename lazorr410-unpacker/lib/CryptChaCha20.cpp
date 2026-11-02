#include "CryptChaCha20.h"
#include <cstring>
#include <algorithm>

namespace paz {

// ── ChaCha20 core ────────────────────────────────────────────────────

static inline uint32_t rotl(uint32_t v, int n) {
    return (v << n) | (v >> (32 - n));
}

static inline uint32_t readLE32(const uint8_t *p) {
    return static_cast<uint32_t>(p[0])
         | (static_cast<uint32_t>(p[1]) << 8)
         | (static_cast<uint32_t>(p[2]) << 16)
         | (static_cast<uint32_t>(p[3]) << 24);
}

static inline void writeLE32(uint8_t *p, uint32_t v) {
    p[0] = static_cast<uint8_t>(v);
    p[1] = static_cast<uint8_t>(v >> 8);
    p[2] = static_cast<uint8_t>(v >> 16);
    p[3] = static_cast<uint8_t>(v >> 24);
}

#define QR(a, b, c, d) \
    a += b; d ^= a; d = rotl(d, 16); \
    c += d; b ^= c; b = rotl(b, 12); \
    a += b; d ^= a; d = rotl(d, 8);  \
    c += d; b ^= c; b = rotl(b, 7);

static void chacha20Block(const uint32_t state[16], uint8_t out[64]) {
    uint32_t s[16];
    memcpy(s, state, 64);

    for (int i = 0; i < 10; i++) {
        QR(s[0], s[4], s[ 8], s[12])
        QR(s[1], s[5], s[ 9], s[13])
        QR(s[2], s[6], s[10], s[14])
        QR(s[3], s[7], s[11], s[15])
        QR(s[0], s[5], s[10], s[15])
        QR(s[1], s[6], s[11], s[12])
        QR(s[2], s[7], s[ 8], s[13])
        QR(s[3], s[4], s[ 9], s[14])
    }

    for (int i = 0; i < 16; i++)
        writeLE32(out + i * 4, s[i] + state[i]);
}

#undef QR

void chacha20(const uint8_t *key, const uint8_t *iv,
              const uint8_t *in, uint8_t *out, uint32_t len) {
    uint32_t state[16];
    state[0] = 0x61707865; // "expand 32-byte k"
    state[1] = 0x3320646e;
    state[2] = 0x79622d32;
    state[3] = 0x6b206574;

    for (int i = 0; i < 8; i++)
        state[4 + i] = readLE32(key + i * 4);

    for (int i = 0; i < 4; i++)
        state[12 + i] = readLE32(iv + i * 4);

    uint8_t block[64];
    uint32_t offset = 0;

    while (offset < len) {
        chacha20Block(state, block);
        uint32_t remaining = std::min(64u, len - offset);
        for (uint32_t i = 0; i < remaining; i++)
            out[offset + i] = in[offset + i] ^ block[i];
        offset += remaining;
        state[12]++;
    }
}

// ── Bob Jenkins' lookup3 hashlittle ──────────────────────────────────

#define mix(a, b, c) \
    a -= c; a ^= rotl(c, 4);  c += b; \
    b -= a; b ^= rotl(a, 6);  a += c; \
    c -= b; c ^= rotl(b, 8);  b += a; \
    a -= c; a ^= rotl(c, 16); c += b; \
    b -= a; b ^= rotl(a, 19); a += c; \
    c -= b; c ^= rotl(b, 4);  b += a;

#define final(a, b, c) \
    c ^= b; c -= rotl(b, 14); \
    a ^= c; a -= rotl(c, 11); \
    b ^= a; b -= rotl(a, 25); \
    c ^= b; c -= rotl(b, 16); \
    a ^= c; a -= rotl(c, 4);  \
    b ^= a; b -= rotl(a, 14); \
    c ^= b; c -= rotl(b, 24);

uint32_t hashlittle(const uint8_t *data, uint32_t length, uint32_t initval) {
    uint32_t a, b, c;
    a = b = c = 0xDEADBEEF + length + initval;

    uint32_t off = 0;
    while (length > 12) {
        a += readLE32(data + off);
        b += readLE32(data + off + 4);
        c += readLE32(data + off + 8);
        mix(a, b, c);
        off += 12;
        length -= 12;
    }

    // Handle the last few bytes
    // Zero-pad a 12-byte tail buffer
    uint8_t tail[12] = {};
    memcpy(tail, data + off, length);

    if (length >= 12) c += readLE32(tail + 8);
    else if (length >= 9) {
        uint32_t v = readLE32(tail + 8);
        c += v & (0xFFFFFFFF >> (8 * (12 - length)));
    }
    if (length >= 8) b += readLE32(tail + 4);
    else if (length >= 5) {
        uint32_t v = readLE32(tail + 4);
        b += v & (0xFFFFFFFF >> (8 * (8 - length)));
    }
    if (length >= 4) a += readLE32(tail + 0);
    else if (length >= 1) {
        uint32_t v = readLE32(tail + 0);
        a += v & (0xFFFFFFFF >> (8 * (4 - length)));
    }
    else return c;

    final(a, b, c);
    return c;
}

#undef mix
#undef final

// ── Key derivation ───────────────────────────────────────────────────

static const uint32_t HASH_INITVAL = 0x000C5EDE;
static const uint32_t IV_XOR = 0x60616263;
static const uint32_t XOR_DELTAS[8] = {
    0x00000000, 0x0A0A0A0A, 0x0C0C0C0C, 0x06060606,
    0x0E0E0E0E, 0x0A0A0A0A, 0x06060606, 0x02020202,
};

static std::string toLowerBasename(const std::string &path) {
    // Find last separator
    size_t pos = path.find_last_of("/\\");
    std::string basename = (pos != std::string::npos) ? path.substr(pos + 1) : path;
    for (auto &c : basename)
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return basename;
}

void deriveKeyIv(const std::string &filename, uint8_t key[32], uint8_t iv[16]) {
    std::string basename = toLowerBasename(filename);
    uint32_t seed = hashlittle(
        reinterpret_cast<const uint8_t *>(basename.data()),
        static_cast<uint32_t>(basename.size()),
        HASH_INITVAL);

    // IV: seed repeated 4 times
    for (int i = 0; i < 4; i++)
        writeLE32(iv + i * 4, seed);

    // Key: 8 chunks of (seed ^ IV_XOR ^ delta[i])
    uint32_t keyBase = seed ^ IV_XOR;
    for (int i = 0; i < 8; i++)
        writeLE32(key + i * 4, keyBase ^ XOR_DELTAS[i]);
}

std::vector<uint8_t> decryptEntry(const uint8_t *ciphertext, uint32_t len,
                                  const std::string &filename) {
    uint8_t key[32], iv[16];
    deriveKeyIv(filename, key, iv);
    std::vector<uint8_t> out(len);
    chacha20(key, iv, ciphertext, out.data(), len);
    return out;
}

} // namespace paz
