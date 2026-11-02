using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;

namespace PazGui.Models;

public static class PamtExtractor
{
    public static async Task<(int Extracted, int Decrypted, int Decompressed)> ExtractAllAsync(
        IReadOnlyList<FileEntry> entries,
        string outputDir,
        IProgress<(int Current, int Total, int Decrypted, int Decompressed)>? progress = null,
        CancellationToken ct = default)
    {
        return await Task.Run(() =>
        {
            int total = entries.Count;
            int decrypted = 0;
            int decompressed = 0;

            for (int i = 0; i < total; i++)
            {
                ct.ThrowIfCancellationRequested();

                var entry = entries[i];
                uint readSize = entry.CompressedSize > 0
                    ? entry.CompressedSize
                    : entry.OriginalSize;

                string relPath = entry.FullPath.Replace('\\', '/');
                string outPath = Path.Combine(outputDir, relPath.Replace('/', Path.DirectorySeparatorChar));

                string? dir = Path.GetDirectoryName(outPath);
                if (dir != null)
                    Directory.CreateDirectory(dir);

                using var paz = new FileStream(entry.PazFilePath, FileMode.Open, FileAccess.Read, FileShare.Read);
                paz.Seek(entry.Offset, SeekOrigin.Begin);

                var buffer = new byte[readSize];
                paz.ReadExactly(buffer);

                // Decrypt XML files using filename-derived key
                if (IsXmlFile(entry.FullPath))
                {
                    string basename = Path.GetFileName(entry.FullPath);
                    buffer = PazNative.Decrypt(buffer, basename);
                    decrypted++;

                    // Decompress after decryption if LZ4
                    if (entry.IsCompressed && entry.CompressionType == 2)
                    {
                        var decompData = PazNative.Lz4Decompress(buffer, entry.OriginalSize);
                        if (decompData != null)
                        {
                            buffer = decompData;
                            decompressed++;
                        }
                    }
                }
                else if (entry.IsCompressed && entry.CompressionType == 2)
                {
                    var decompData = PazNative.Lz4Decompress(buffer, entry.OriginalSize);
                    if (decompData != null)
                    {
                        buffer = decompData;
                        decompressed++;
                    }
                }

                File.WriteAllBytes(outPath, buffer);

                if (i % 50 == 0 || i == total - 1)
                    progress?.Report((i + 1, total, decrypted, decompressed));
            }

            return (total, decrypted, decompressed);
        }, ct);
    }

    private static bool IsXmlFile(string path)
    {
        return path.EndsWith(".xml", StringComparison.OrdinalIgnoreCase);
    }
}
