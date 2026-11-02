using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace PazGui.Models;

public record FileEntry(
    string FullPath,
    string PazFilePath,
    uint Offset,
    uint CompressedSize,
    uint OriginalSize,
    uint Flags
)
{
    /// <summary>Compression type from PAMT flags: 0=none, 2=LZ4, 3=custom, 4=zlib.</summary>
    public int CompressionType => (int)((Flags >> 8) & 0x0F);

    /// <summary>Whether the entry is compressed (comp_size != orig_size).</summary>
    public bool IsCompressed => CompressedSize != OriginalSize && CompressedSize > 0;
}

public static class PamtParser
{
    public static List<FileEntry> Parse(string pamtPath, string pazDir)
    {
        var data = File.ReadAllBytes(pamtPath);
        if (data.Length < 16)
            throw new InvalidDataException($"PAMT file too small: {pamtPath}");

        int off = 0;
        off += 4; // magic

        // PAZ count
        uint pazCount = BitConverter.ToUInt32(data, off);
        off += 4;
        off += 4; // hash
        off += 4; // zero

        // PAZ table: pazCount entries of [hash:4][size:4] with [sep:4] between
        string stem = Path.GetFileNameWithoutExtension(pamtPath);
        int baseNum = int.Parse(stem);
        var pazPaths = new string[pazCount];
        for (int i = 0; i < (int)pazCount; i++)
        {
            off += 4; // hash
            off += 4; // size
            pazPaths[i] = Path.Combine(pazDir, (baseNum + i) + ".paz");
            if (i < (int)pazCount - 1)
                off += 4; // separator
        }

        // Folder section: [size:4][entries...]
        uint folderSectionSize = BitConverter.ToUInt32(data, off);
        off += 4;

        string folderPrefix = "";
        int folderEnd = off + (int)folderSectionSize;
        while (off < folderEnd)
        {
            uint parent = BitConverter.ToUInt32(data, off);
            byte slen = data[off + 4];
            string name = Encoding.ASCII.GetString(data, off + 5, slen);
            if (parent == 0xFFFFFFFF)
                folderPrefix = name;
            off += 5 + slen;
        }

        // Node section: [size:4][entries...]
        uint nodeSectionSize = BitConverter.ToUInt32(data, off);
        off += 4;

        int nodeStart = off;
        int nodeEnd = off + (int)nodeSectionSize;

        var nodes = new Dictionary<uint, (uint Parent, string Name)>();
        while (off < nodeEnd)
        {
            uint relOff = (uint)(off - nodeStart);
            uint parent = BitConverter.ToUInt32(data, off);
            byte slen = data[off + 4];
            string name = Encoding.ASCII.GetString(data, off + 5, slen);
            nodes[relOff] = (parent, name);
            off += 5 + slen;
        }

        // Record section: [folderCount:4][hash:4][folderRecs...][fileRecs...]
        uint folderCount = BitConverter.ToUInt32(data, off);
        off += 4;
        off += 4; // hash
        off += (int)folderCount * 16;

        // File records: 20 bytes each
        // [nodeRef:4][pazOffset:4][compSize:4][origSize:4][flags:4]
        // flags & 0xFF = PAZ file index
        var entries = new List<FileEntry>();
        while (off + 20 <= data.Length)
        {
            uint nodeRef = BitConverter.ToUInt32(data, off);
            uint pazOffset = BitConverter.ToUInt32(data, off + 4);
            uint compSize = BitConverter.ToUInt32(data, off + 8);
            uint origSize = BitConverter.ToUInt32(data, off + 12);
            uint flags = BitConverter.ToUInt32(data, off + 16);

            uint pazIndex = flags & 0xFF;
            string pazFile = pazIndex < pazPaths.Length ? pazPaths[pazIndex] : pazPaths[0];

            string nodePath = BuildPath(nodes, nodeRef);
            string fullPath = string.IsNullOrEmpty(folderPrefix)
                ? nodePath
                : folderPrefix + "/" + nodePath;

            entries.Add(new FileEntry(fullPath, pazFile, pazOffset, compSize, origSize, flags >> 8));
            off += 20;
        }

        return entries;
    }

    private static string BuildPath(Dictionary<uint, (uint Parent, string Name)> nodes, uint nodeRef)
    {
        var parts = new List<string>();
        uint cur = nodeRef;
        var seen = new HashSet<uint>();

        while (cur != 0xFFFFFFFF && nodes.ContainsKey(cur))
        {
            if (!seen.Add(cur)) break;
            var (parent, name) = nodes[cur];
            parts.Add(name);
            cur = parent;
        }

        parts.Reverse();
        return string.Concat(parts);
    }
}
