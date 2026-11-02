using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using PazGui.Models;

namespace PazGui.ViewModels;

public partial class MainWindowViewModel : ViewModelBase
{
    [ObservableProperty] private string _statusText = "No archives loaded.";
    [ObservableProperty] private string _detailText = "Select a file to see details.";
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanExtract))]
    private bool _isExtracting;
    [ObservableProperty] private int _progressValue;
    [ObservableProperty] private int _progressMax = 100;
    [ObservableProperty] private FileTreeNodeViewModel? _selectedNode;
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasSearchFilter))]
    private string _searchText = "";

    public ObservableCollection<FileTreeNodeViewModel> TreeRoots { get; } = new();

    private readonly List<FileEntry> _allEntries = new();
    private readonly Dictionary<string, FileEntry> _entryByPath = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<FileTreeNodeViewModel> _fullTreeRoots = new();

    partial void OnSearchTextChanged(string value)
    {
        ApplyFilter(value);
    }

    private void ApplyFilter(string filter)
    {
        TreeRoots.Clear();
        if (string.IsNullOrWhiteSpace(filter))
        {
            foreach (var r in _fullTreeRoots)
                TreeRoots.Add(r);
            return;
        }

        // For search, we filter from _allEntries (flat list) and build a fresh tree
        var matches = new List<FileEntry>();
        foreach (var entry in _allEntries)
        {
            string name = Path.GetFileName(entry.FullPath);
            if (name.Contains(filter, StringComparison.OrdinalIgnoreCase))
                matches.Add(entry);
        }

        if (matches.Count == 0)
        {
            StatusText = "No matching files.";
            return;
        }

        // Build a small tree from matches (fully expanded, no lazy load needed)
        var root = new FileTreeNodeViewModel("Results");
        foreach (var entry in matches)
        {
            string fullPath = entry.FullPath.Replace('\\', '/');
            string[] parts = fullPath.Split('/', StringSplitOptions.RemoveEmptyEntries);
            var current = root;
            for (int i = 0; i < parts.Length - 1; i++)
                current = current.GetOrCreateFolder(parts[i]);

            string fileName = parts.Length > 0 ? parts[^1] : fullPath;
            string pazSource = GetPazSourceLabel(entry.PazFilePath);
            var fileNode = new FileTreeNodeViewModel(fileName, entry.CompressedSize, entry.OriginalSize,
                Path.GetFileNameWithoutExtension(entry.PazFilePath), pazSource)
            { FullPath = fullPath };
            current.Children.Add(fileNode);
        }

        // Set file counts on search result folders
        SetFileCountsRecursive(root);

        if (root.Children.Count == 1)
            TreeRoots.Add(root.Children[0]);
        else
            foreach (var child in root.Children)
                TreeRoots.Add(child);

        StatusText = $"Showing {matches.Count:N0} matching file(s).";
    }

    partial void OnSelectedNodeChanged(FileTreeNodeViewModel? value)
    {
        if (value == null)
        {
            DetailText = "Select a file to see details.";
            return;
        }

        // Handle "Load more..." click
        if (value.IsLoadMore)
        {
            // Find the parent folder that owns this placeholder
            var parent = FindParentOf(value, _fullTreeRoots);
            if (parent == null)
                parent = FindParentOf(value, new List<FileTreeNodeViewModel>(TreeRoots));
            if (parent != null)
            {
                parent.LoadNextPage();
                // Re-select the parent so the detail panel updates
                SelectedNode = parent;
            }
            return;
        }

        if (value.IsFile)
        {
            string info = $"File: {value.Name}\n" +
                          $"Type: {value.FileType}\n" +
                          $"Stored Size: {value.CompressedSize:N0} bytes\n" +
                          $"Original Size: {value.OriginalSize:N0} bytes\n" +
                          $"Archive: {value.ArchiveName}\n" +
                          $"Source: {value.PazSource}";

            if (value.CompressedSize < value.OriginalSize && value.CompressedSize > 0)
            {
                double ratio = 100.0 * value.CompressedSize / value.OriginalSize;
                info += $"\nNote: Stored at reduced quality ({ratio:F1}% of original)";
            }

            DetailText = info;
        }
        else
        {
            DetailText = $"Folder: {value.Name}\n" +
                         $"Contents: {value.TotalFileCount:N0} file(s)";
        }
    }

    private static FileTreeNodeViewModel? FindParentOf(FileTreeNodeViewModel target, IEnumerable<FileTreeNodeViewModel> roots)
    {
        foreach (var root in roots)
        {
            if (root.Children.Contains(target)) return root;
            var found = FindParentOf(target, root.Children);
            if (found != null) return found;
        }
        return null;
    }

    /// <summary>
    /// Called when a TreeView node is expanded. Populates lazy children.
    /// </summary>
    public void OnNodeExpanded(FileTreeNodeViewModel node)
    {
        if (node.IsFile || node.IsLoadMore || node.IsExpanded) return;
        node.IsExpanded = true;

        if (!node.HasDummyChild) return;

        // Remove the dummy placeholder
        node.Children.Clear();

        // Populate from PendingChildren with pagination
        if (node.PendingChildren != null)
        {
            node.LoadNextPage();
        }
    }

    public void LoadFolder(string dirPath)
    {
        TreeRoots.Clear();
        _allEntries.Clear();
        _entryByPath.Clear();
        _fullTreeRoots.Clear();

        // Stage 1: Parse all PAMT files and collect entries
        int archiveCount = 0;
        foreach (var pamtFile in Directory.EnumerateFiles(dirPath, "*.pamt", SearchOption.AllDirectories))
        {
            string baseName = Path.GetFileNameWithoutExtension(pamtFile);
            string pamtDir = Path.GetDirectoryName(pamtFile)!;

            try
            {
                var entries = PamtParser.Parse(pamtFile, pamtDir);
                foreach (var entry in entries)
                {
                    _allEntries.Add(entry);
                    _entryByPath[entry.FullPath.Replace('\\', '/')] = entry;
                }
                archiveCount++;
            }
            catch (Exception ex)
            {
                StatusText = $"Error parsing {baseName}: {ex.Message}";
            }
        }

        // Stage 2: Build a staging tree (fully populated, used to compute counts)
        var staging = new FileTreeNodeViewModel("Archives");
        foreach (var entry in _allEntries)
        {
            string fullPath = entry.FullPath.Replace('\\', '/');
            string[] parts = fullPath.Split('/', StringSplitOptions.RemoveEmptyEntries);
            var current = staging;
            for (int i = 0; i < parts.Length - 1; i++)
                current = current.GetOrCreateFolder(parts[i]);

            string fileName = parts.Length > 0 ? parts[^1] : fullPath;
            string pazSource = GetPazSourceLabel(entry.PazFilePath);
            string archiveName = Path.GetFileNameWithoutExtension(entry.PazFilePath);
            var fileNode = new FileTreeNodeViewModel(fileName, entry.CompressedSize, entry.OriginalSize, archiveName, pazSource)
            { FullPath = fullPath };
            current.Children.Add(fileNode);
        }

        // Stage 3: Compute file counts and convert to lazy tree
        SetFileCountsRecursive(staging);
        var lazyRoots = ConvertToLazy(staging);

        if (lazyRoots.Count == 1)
        {
            TreeRoots.Add(lazyRoots[0]);
        }
        else
        {
            foreach (var child in lazyRoots)
                TreeRoots.Add(child);
        }

        foreach (var r in TreeRoots)
            _fullTreeRoots.Add(r);

        SearchText = "";
        StatusText = $"Loaded {archiveCount} archive(s), {_allEntries.Count:N0} files.";
        OnPropertyChanged(nameof(CanExtract));
    }

    /// <summary>
    /// Recursively compute TotalFileCount for all folder nodes.
    /// </summary>
    private static int SetFileCountsRecursive(FileTreeNodeViewModel node)
    {
        if (node.IsFile) return 1;
        int count = 0;
        foreach (var child in node.Children)
            count += SetFileCountsRecursive(child);
        node.TotalFileCount = count;
        return count;
    }

    /// <summary>
    /// Convert a fully-built staging tree into a lazy-loaded tree.
    /// Top-level children are materialized; deeper children use dummy placeholders.
    /// </summary>
    private static List<FileTreeNodeViewModel> ConvertToLazy(FileTreeNodeViewModel staging)
    {
        var result = new List<FileTreeNodeViewModel>();
        foreach (var child in staging.Children)
        {
            if (child.IsFile)
            {
                result.Add(child);
            }
            else
            {
                var lazyFolder = new FileTreeNodeViewModel(child.Name)
                {
                    TotalFileCount = child.TotalFileCount
                };
                PrepareLazyChildren(lazyFolder, child);
                result.Add(lazyFolder);
            }
        }
        return result;
    }

    /// <summary>
    /// Store the staging node's children as PendingChildren on the lazy node,
    /// recursively making sub-folders lazy too. Adds a dummy child for the expand arrow.
    /// </summary>
    private static void PrepareLazyChildren(FileTreeNodeViewModel lazyNode, FileTreeNodeViewModel stagingNode)
    {
        var children = new List<FileTreeNodeViewModel>();
        foreach (var child in stagingNode.Children)
        {
            if (child.IsFile)
            {
                children.Add(child);
            }
            else
            {
                var lazyChild = new FileTreeNodeViewModel(child.Name)
                {
                    TotalFileCount = child.TotalFileCount
                };
                PrepareLazyChildren(lazyChild, child);
                children.Add(lazyChild);
            }
        }

        lazyNode.PendingChildren = children.ToArray();
        lazyNode.LoadedCount = 0;
        lazyNode.AddDummyChild();
    }

    public bool CanExtract => _allEntries.Count > 0 && !IsExtracting;

    public async Task ExtractAllAsync(string outputDir)
    {
        await ExtractEntriesAsync(_allEntries, outputDir, "all");
    }

    public async Task ExtractSelectedAsync(string outputDir)
    {
        if (SelectedNode == null) return;
        var entries = CollectEntries(SelectedNode);
        await ExtractEntriesAsync(entries, outputDir, "selected");
    }

    public async Task ExtractSearchResultsAsync(string outputDir)
    {
        var entries = new List<FileEntry>();
        foreach (var root in TreeRoots)
            CollectEntriesFromNode(root, entries);
        await ExtractEntriesAsync(entries, outputDir, "search results");
    }

    public bool HasSearchFilter => !string.IsNullOrWhiteSpace(SearchText);

    public async Task DumpFileListAsync(string outputPath)
    {
        var lines = new List<string>(_allEntries.Count);
        foreach (var entry in _allEntries)
            lines.Add(entry.FullPath.Replace('\\', '/'));
        lines.Sort(StringComparer.OrdinalIgnoreCase);

        await File.WriteAllLinesAsync(outputPath, lines);
        StatusText = $"Dumped {lines.Count:N0} file path(s) to {Path.GetFileName(outputPath)}";
    }

    private List<FileEntry> CollectEntries(FileTreeNodeViewModel node)
    {
        var entries = new List<FileEntry>();
        CollectEntriesFromNode(node, entries);
        return entries;
    }

    private void CollectEntriesFromNode(FileTreeNodeViewModel node, List<FileEntry> entries)
    {
        if (node.IsLoadMore) return;
        if (node.IsFile)
        {
            if (_entryByPath.TryGetValue(node.FullPath, out var entry))
                entries.Add(entry);
        }
        else
        {
            // If the node has pending (not yet loaded) children, collect from those too
            if (node.PendingChildren != null)
            {
                foreach (var child in node.PendingChildren)
                    CollectEntriesFromNode(child, entries);
            }
            else
            {
                foreach (var child in node.Children)
                    CollectEntriesFromNode(child, entries);
            }
        }
    }

    private async Task ExtractEntriesAsync(IReadOnlyList<FileEntry> entries, string outputDir, string label)
    {
        if (entries.Count == 0) return;

        IsExtracting = true;
        ProgressMax = entries.Count;
        ProgressValue = 0;
        StatusText = "Extracting...";

        var progress = new Progress<(int Current, int Total, int Decrypted, int Decompressed)>(p =>
        {
            ProgressValue = p.Current;
            var parts = new List<string>();
            if (p.Decrypted > 0) parts.Add($"{p.Decrypted} decrypted");
            if (p.Decompressed > 0) parts.Add($"{p.Decompressed} decompressed");
            string extra = parts.Count > 0 ? $" ({string.Join(", ", parts)})" : "";
            StatusText = $"Extracting... {p.Current}/{p.Total}{extra}";
        });

        try
        {
            var (extracted, decrypted, decompressed) = await PamtExtractor.ExtractAllAsync(
                entries, outputDir, progress: progress, ct: CancellationToken.None);

            string summary = $"Extracted {extracted} {label} file(s) to {outputDir}";
            var details = new List<string>();
            if (decrypted > 0) details.Add($"{decrypted} XML decrypted");
            if (decompressed > 0) details.Add($"{decompressed} LZ4 decompressed");
            if (details.Count > 0) summary += $" — {string.Join(", ", details)}";
            StatusText = summary;
        }
        catch (Exception ex)
        {
            StatusText = $"Extraction failed: {ex.Message}";
        }
        finally
        {
            IsExtracting = false;
        }
    }

    private static string GetPazSourceLabel(string pazFilePath)
    {
        var parts = pazFilePath.Replace('\\', '/').Split('/', StringSplitOptions.RemoveEmptyEntries);
        return parts.Length >= 2
            ? parts[^2] + "/" + parts[^1]
            : parts.Length == 1 ? parts[0] : pazFilePath;
    }
}
