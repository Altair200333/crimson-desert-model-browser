#pragma once

#include "PazTypes.h"
#include <string>
#include <vector>
#include <functional>

namespace paz {

// Extract a single PAMT file entry to a byte buffer.
// No ICE decryption — data is raw or compressed only.
std::vector<uint8_t> pamtExtractToMemory(const FileEntry &entry);

// Extract a single PAMT file entry to disk.
bool pamtExtractToFile(const FileEntry &entry, const std::string &outputPath);

// Callback: (currentIndex, totalCount)
using ProgressCallback = std::function<void(uint32_t, uint32_t)>;

// Extract all PAMT entries under outputDir, preserving folder structure.
void pamtExtractAll(const std::vector<FileEntry> &entries,
                    const std::string &outputDir,
                    ProgressCallback progress = nullptr);

} // namespace paz
