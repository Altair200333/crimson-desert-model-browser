using System;
using System.Runtime.InteropServices;

namespace PazGui.Models;

/// <summary>
/// P/Invoke wrapper for paz-native.dll (ChaCha20 decryption + LZ4 decompression).
/// Keys are derived from the filename — no key database needed.
/// </summary>
public static class PazNative
{
    private const string DllName = "paz-native";

    [DllImport(DllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern void paz_decrypt(
        [MarshalAs(UnmanagedType.LPUTF8Str)] string filename,
        byte[] ciphertext, uint len, byte[] output);

    [DllImport(DllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern uint paz_lz4_decompress(
        byte[] src, uint srcLen, byte[] dst, uint dstSize);

    /// <summary>
    /// Decrypt a PAZ entry using a filename-derived ChaCha20 key.
    /// </summary>
    public static byte[] Decrypt(byte[] ciphertext, string filename)
    {
        var output = new byte[ciphertext.Length];
        paz_decrypt(filename, ciphertext, (uint)ciphertext.Length, output);
        return output;
    }

    /// <summary>
    /// LZ4 block decompression.
    /// Returns decompressed bytes or null on failure.
    /// </summary>
    public static byte[]? Lz4Decompress(byte[] compressed, uint originalSize)
    {
        var output = new byte[originalSize];
        uint actual = paz_lz4_decompress(compressed, (uint)compressed.Length, output, originalSize);
        if (actual == 0) return null;
        if (actual < originalSize)
            Array.Resize(ref output, (int)actual);
        return output;
    }
}
