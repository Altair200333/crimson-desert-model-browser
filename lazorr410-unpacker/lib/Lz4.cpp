#include "Lz4.h"
#include <algorithm>

namespace paz {

std::vector<uint8_t> lz4Decompress(const uint8_t *src, uint32_t srcLen, uint32_t dstSize) {
    std::vector<uint8_t> out;
    out.reserve(dstSize);

    uint32_t i = 0;

    while (i < srcLen && out.size() < dstSize) {
        uint8_t token = src[i++];

        // Literal length from high nibble
        uint32_t litLen = (token >> 4) & 0x0F;
        if (litLen == 15) {
            while (i < srcLen) {
                uint8_t extra = src[i++];
                litLen += extra;
                if (extra != 255) break;
            }
        }

        // Copy literal bytes
        if (i + litLen > srcLen) {
            out.insert(out.end(), src + i, src + srcLen);
            break;
        }
        out.insert(out.end(), src + i, src + i + litLen);
        i += litLen;

        if (i >= srcLen) break;

        // Match offset (2 bytes LE)
        if (i + 2 > srcLen) break;
        uint32_t offset = src[i] | (static_cast<uint32_t>(src[i + 1]) << 8);
        i += 2;

        if (offset == 0) break;

        // Match length from low nibble + 4 (minimum match)
        uint32_t matchLen = (token & 0x0F) + 4;
        if ((token & 0x0F) == 15) {
            while (i < srcLen) {
                uint8_t extra = src[i++];
                matchLen += extra;
                if (extra != 255) break;
            }
        }

        if (offset > out.size()) return {};

        uint32_t start = static_cast<uint32_t>(out.size()) - offset;
        for (uint32_t j = 0; j < matchLen; j++) {
            out.push_back(out[start + (j % offset)]);
        }
    }

    return out;
}

} // namespace paz
