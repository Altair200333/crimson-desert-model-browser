#include "PamtFile.h"
#include <fstream>
#include <stdexcept>
#include <cstring>
#include <unordered_map>
#include <filesystem>

namespace fs = std::filesystem;

namespace paz {

struct PathNode {
    uint32_t parentOffset; // 0xFFFFFFFF = root
    std::string name;
};

static std::string buildPath(
    const std::unordered_map<uint32_t, PathNode> &nodes,
    uint32_t nodeRef)
{
    std::string parts[64];
    int depth = 0;
    uint32_t cur = nodeRef;

    while (cur != 0xFFFFFFFF && depth < 64) {
        auto it = nodes.find(cur);
        if (it == nodes.end()) break;
        parts[depth++] = it->second.name;
        cur = it->second.parentOffset;
    }

    std::string result;
    for (int i = depth - 1; i >= 0; --i)
        result += parts[i];
    return result;
}

std::vector<FileEntry> parsePamtFile(const std::string &pamtPath,
                                      const std::string &pazDir) {
    std::ifstream file(pamtPath, std::ios::binary);
    if (!file.is_open())
        throw std::runtime_error("Cannot open PAMT file: " + pamtPath);

    file.seekg(0, std::ios::end);
    size_t fileSize = static_cast<size_t>(file.tellg());
    file.seekg(0);
    std::vector<uint8_t> data(fileSize);
    file.read(reinterpret_cast<char *>(data.data()), fileSize);

    if (fileSize < 16)
        throw std::runtime_error("PAMT file too small: " + pamtPath);

    // Header: [magic:4][pazCount:4][hash:4][zero:4]
    size_t off = 0;
    // uint32_t magic;
    // memcpy(&magic, data.data(), 4);
    off += 4;

    uint32_t pazCount;
    memcpy(&pazCount, data.data() + off, 4);
    off += 4;

    off += 4; // hash
    off += 4; // zero

    // PAZ table: pazCount entries of [hash:4][size:4]
    // with [separator:4] between consecutive entries.
    // Build list of PAZ file paths: <pazDir>/<pamtStem>.paz for index 0,
    // then <pazDir>/<index>.paz for all.
    // Actually, the naming convention is N.paz where N matches the pamt stem number.
    // For pamt "0.pamt" -> "0.paz", "1.paz", "2.paz"
    fs::path pamtFsPath(pamtPath);
    std::string stem = pamtFsPath.stem().string();

    std::vector<std::string> pazPaths(pazCount);
    for (uint32_t i = 0; i < pazCount; i++) {
        // Read hash and size (we don't use them, just skip)
        off += 4; // hash
        off += 4; // size

        // Build PAZ path: <pazDir>/<stem + i>.paz
        // e.g. stem="0", i=0 -> "0.paz", i=1 -> "1.paz", i=2 -> "2.paz"
        int pazNum = std::stoi(stem) + static_cast<int>(i);
        pazPaths[i] = (fs::path(pazDir) / (std::to_string(pazNum) + ".paz")).string();

        // Skip separator between entries (not after the last)
        if (i < pazCount - 1)
            off += 4;
    }

    // Folder section: [size:4][entries...]
    uint32_t folderSectionSize;
    memcpy(&folderSectionSize, data.data() + off, 4);
    off += 4;

    std::string folderPrefix;
    size_t folderEnd = off + folderSectionSize;
    while (off < folderEnd) {
        uint32_t parent;
        memcpy(&parent, data.data() + off, 4);
        uint8_t slen = data[off + 4];
        std::string name(reinterpret_cast<const char *>(data.data() + off + 5), slen);
        if (parent == 0xFFFFFFFF)
            folderPrefix = name;
        off += 5 + slen;
    }

    // Node section: [size:4][entries...]
    uint32_t nodeSectionSize;
    memcpy(&nodeSectionSize, data.data() + off, 4);
    off += 4;

    size_t nodeStart = off;
    size_t nodeEnd = off + nodeSectionSize;

    std::unordered_map<uint32_t, PathNode> nodes;
    while (off < nodeEnd) {
        uint32_t relOff = static_cast<uint32_t>(off - nodeStart);
        uint32_t parent;
        memcpy(&parent, data.data() + off, 4);
        uint8_t slen = data[off + 4];
        std::string name(reinterpret_cast<const char *>(data.data() + off + 5), slen);
        nodes[relOff] = {parent, std::move(name)};
        off += 5 + slen;
    }

    // Record section: [folderCount:4][hash:4][folderRecs: folderCount*16][fileRecs...]
    uint32_t folderCount;
    memcpy(&folderCount, data.data() + off, 4);
    off += 4;
    off += 4; // hash
    off += folderCount * 16; // folder records

    // File records: 20 bytes each
    // [nodeRef:4][pazOffset:4][compSize:4][origSize:4][flags:4]
    // flags & 0xFF = PAZ file index
    std::vector<FileEntry> entries;
    while (off + 20 <= fileSize) {
        uint32_t nodeRef, pazOffset, compSize, origSize, flags;
        memcpy(&nodeRef, data.data() + off, 4);
        memcpy(&pazOffset, data.data() + off + 4, 4);
        memcpy(&compSize, data.data() + off + 8, 4);
        memcpy(&origSize, data.data() + off + 12, 4);
        memcpy(&flags, data.data() + off + 16, 4);

        uint32_t pazIndex = flags & 0xFF;

        FileEntry e;
        std::string nodePath = buildPath(nodes, nodeRef);
        e.fullPath = folderPrefix.empty() ? nodePath : (folderPrefix + "/" + nodePath);
        e.pazFilePath = (pazIndex < pazPaths.size()) ? pazPaths[pazIndex] : pazPaths[0];
        e.offset = pazOffset;
        e.compressedSize = compSize;
        e.originalSize = origSize;
        e.flags = flags >> 8;

        entries.push_back(std::move(e));
        off += 20;
    }

    return entries;
}

} // namespace paz
