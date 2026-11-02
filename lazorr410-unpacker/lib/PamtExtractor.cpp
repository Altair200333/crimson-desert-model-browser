#include "PamtExtractor.h"
#include <fstream>
#include <cstring>
#include <filesystem>

namespace fs = std::filesystem;

namespace paz {

std::vector<uint8_t> pamtExtractToMemory(const FileEntry &entry) {
    std::ifstream pazFile(entry.pazFilePath, std::ios::binary);
    if (!pazFile.is_open())
        return {};

    // PAMT format stores files as-is in the PAZ (no encryption).
    // compressedSize is the actual stored size; originalSize may be larger
    // (e.g. full-mip DDS) but the stored data is already a valid file.
    uint32_t readSize = entry.compressedSize > 0
                        ? entry.compressedSize : entry.originalSize;

    std::vector<uint8_t> raw(readSize);
    pazFile.seekg(entry.offset);
    pazFile.read(reinterpret_cast<char *>(raw.data()), readSize);

    return raw;
}

bool pamtExtractToFile(const FileEntry &entry, const std::string &outputPath) {
    try {
        auto data = pamtExtractToMemory(entry);
        if (data.empty() && entry.originalSize > 0)
            return false;

        fs::path p(outputPath);
        if (p.has_parent_path())
            fs::create_directories(p.parent_path());

        std::ofstream out(outputPath, std::ios::binary);
        if (!out.is_open()) return false;
        out.write(reinterpret_cast<const char *>(data.data()), data.size());
        return true;
    } catch (...) {
        return false;
    }
}

static std::string normalizePath(const std::string &path) {
    std::string result = path;
    for (auto &c : result) {
        if (c == '\\') c = '/';
    }
    return result;
}

void pamtExtractAll(const std::vector<FileEntry> &entries,
                    const std::string &outputDir,
                    ProgressCallback progress) {
    uint32_t total = static_cast<uint32_t>(entries.size());
    uint32_t idx = 0;

    for (auto &entry : entries) {
        std::string relPath = normalizePath(entry.fullPath);
        fs::path outPath = fs::path(outputDir) / relPath;
        pamtExtractToFile(entry, outPath.string());

        idx++;
        if (progress) progress(idx, total);
    }
}

} // namespace paz
