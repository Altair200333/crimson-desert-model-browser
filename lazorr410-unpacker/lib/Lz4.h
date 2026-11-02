#pragma once

#include <cstdint>
#include <vector>

namespace paz {

/// LZ4 block decompression (no frame header).
/// Returns decompressed data, or empty vector on failure.
/// @param src compressed data
/// @param srcLen compressed data length
/// @param dstSize expected decompressed size (from PAMT orig_size)
std::vector<uint8_t> lz4Decompress(const uint8_t *src, uint32_t srcLen, uint32_t dstSize);

} // namespace paz
