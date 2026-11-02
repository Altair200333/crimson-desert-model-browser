#pragma once

#include <cstdint>
#include <string>

namespace paz {

struct FileEntry {
    std::string fullPath;
    std::string pazFilePath; // filesystem path to the .PAZ file
    uint32_t offset = 0;
    uint32_t compressedSize = 0;
    uint32_t originalSize = 0;
    uint32_t flags = 0; // raw PAMT flags >> 8
};

} // namespace paz
