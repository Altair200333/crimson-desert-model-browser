using System;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;

namespace PazGui.ViewModels;

public class FileTreeNodeViewModel : ViewModelBase
{
    public string Name { get; }
    public string FullPath { get; set; } = "";
    public bool IsFile { get; }
    public uint CompressedSize { get; }
    public uint OriginalSize { get; }
    public string FileType { get; }
    public string ArchiveName { get; }
    public string PazSource { get; }
    public ObservableCollection<FileTreeNodeViewModel> Children { get; } = new();

    /// <summary>Total file count (set during tree build for folders).</summary>
    public int TotalFileCount { get; set; }

    /// <summary>Backing store of all children before lazy pagination.</summary>
    internal FileTreeNodeViewModel[]? PendingChildren { get; set; }

    /// <summary>How many of PendingChildren have been loaded into Children.</summary>
    internal int LoadedCount { get; set; }

    /// <summary>Whether this node has been expanded (children populated).</summary>
    internal bool IsExpanded { get; set; }

    /// <summary>Whether this is a "Load more..." placeholder.</summary>
    public bool IsLoadMore { get; }

    /// <summary>Page size for lazy pagination.</summary>
    internal const int PageSize = 500;

    // Folder constructor
    public FileTreeNodeViewModel(string name)
    {
        Name = name;
        IsFile = false;
        IsLoadMore = false;
        FileType = "";
        ArchiveName = "";
        PazSource = "";
    }

    // File constructor
    public FileTreeNodeViewModel(string name, uint compressedSize, uint originalSize, string archiveName, string pazSource)
    {
        Name = name;
        IsFile = true;
        IsLoadMore = false;
        CompressedSize = compressedSize;
        OriginalSize = originalSize;
        ArchiveName = archiveName;
        PazSource = pazSource;

        string ext = Path.GetExtension(name).ToLowerInvariant();
        FileType = ext switch
        {
            ".dds" => "DDS Texture",
            ".xml" => "XML",
            ".lua" or ".luac" => "Lua Script",
            ".pac" => "PAC Archive",
            ".pamlod" => "PAM LOD",
            ".png" => "PNG Image",
            ".tga" => "TGA Image",
            ".wav" => "WAV Audio",
            ".ogg" => "OGG Audio",
            ".txt" => "Text",
            ".csv" => "CSV",
            ".json" => "JSON",
            _ => string.IsNullOrEmpty(ext) ? "Unknown" : ext[1..].ToUpperInvariant()
        };
    }

    // "Load more..." placeholder constructor
    private FileTreeNodeViewModel(string label, bool isLoadMore)
    {
        Name = label;
        IsFile = false;
        IsLoadMore = true;
        FileType = "";
        ArchiveName = "";
        PazSource = "";
    }

    public static FileTreeNodeViewModel CreateLoadMorePlaceholder(int remaining)
        => new($"Load more... ({remaining:N0} remaining)", isLoadMore: true);

    /// <summary>Dummy placeholder so the expand arrow appears before lazy load.</summary>
    private static readonly FileTreeNodeViewModel _dummy = new("__dummy__");
    internal static FileTreeNodeViewModel Dummy => _dummy;

    public bool HasDummyChild => Children.Count == 1 && ReferenceEquals(Children[0], _dummy);

    /// <summary>
    /// Add a dummy child so the TreeView shows an expand arrow.
    /// Call this instead of populating children eagerly.
    /// </summary>
    public void AddDummyChild()
    {
        Children.Add(_dummy);
    }

    /// <summary>
    /// Load the next page of children from PendingChildren.
    /// </summary>
    public void LoadNextPage()
    {
        if (PendingChildren == null) return;

        // Remove "Load more..." placeholder if present
        if (Children.Count > 0 && Children[^1].IsLoadMore)
            Children.RemoveAt(Children.Count - 1);

        int end = Math.Min(LoadedCount + PageSize, PendingChildren.Length);
        for (int i = LoadedCount; i < end; i++)
            Children.Add(PendingChildren[i]);
        LoadedCount = end;

        int remaining = PendingChildren.Length - LoadedCount;
        if (remaining > 0)
            Children.Add(CreateLoadMorePlaceholder(remaining));
        else
            PendingChildren = null; // fully loaded, free memory
    }

    public string SizeDisplay => IsFile ? FormatSize(CompressedSize) : "";

    public string SourceDisplay => IsFile ? PazSource : "";

    public string TypeDisplay
    {
        get
        {
            if (IsLoadMore) return "";
            if (IsFile) return FileType;
            return $"Folder ({TotalFileCount:N0})";
        }
    }

    public FileTreeNodeViewModel GetOrCreateFolder(string name)
    {
        var existing = Children.FirstOrDefault(c => !c.IsFile && c.Name == name);
        if (existing != null) return existing;

        var folder = new FileTreeNodeViewModel(name);
        Children.Add(folder);
        return folder;
    }

    private static string FormatSize(uint bytes)
    {
        if (bytes < 1024) return $"{bytes} B";
        if (bytes < 1024 * 1024) return $"{bytes / 1024.0:F1} KB";
        return $"{bytes / (1024.0 * 1024.0):F1} MB";
    }
}
