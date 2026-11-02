using System.Linq;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Platform.Storage;
using PazGui.ViewModels;

namespace PazGui.Views;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        FileTree.ContainerPrepared += OnTreeContainerPrepared;
    }

    private MainWindowViewModel VM => (MainWindowViewModel)DataContext!;

    private void OnTreeContainerPrepared(object? sender, ContainerPreparedEventArgs e)
    {
        if (e.Container is TreeViewItem item)
        {
            item.SetValue(TreeViewItem.IsExpandedProperty, false);
            item.PropertyChanged += OnTreeViewItemPropertyChanged;
        }
    }

    private void OnTreeViewItemPropertyChanged(object? sender, Avalonia.AvaloniaPropertyChangedEventArgs e)
    {
        if (e.Property == TreeViewItem.IsExpandedProperty && e.NewValue is true)
        {
            if (sender is TreeViewItem item && item.DataContext is FileTreeNodeViewModel node)
            {
                VM.OnNodeExpanded(node);
            }
        }
    }

    private async void OnOpenFolder(object? sender, RoutedEventArgs e)
    {
        var folders = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions
        {
            Title = "Select folder containing .pamt files",
            AllowMultiple = false
        });

        var folder = folders.FirstOrDefault();
        if (folder == null) return;

        var path = folder.TryGetLocalPath();
        if (path != null)
            VM.LoadFolder(path);
    }

    private async void OnExtractAll(object? sender, RoutedEventArgs e)
    {
        var path = await PickOutputFolder();
        if (path != null)
            await VM.ExtractAllAsync(path);
    }

    private async void OnExtractSelected(object? sender, RoutedEventArgs e)
    {
        if (VM.SelectedNode == null) return;
        var path = await PickOutputFolder();
        if (path != null)
            await VM.ExtractSelectedAsync(path);
    }

    private async void OnExtractResults(object? sender, RoutedEventArgs e)
    {
        var path = await PickOutputFolder();
        if (path != null)
            await VM.ExtractSearchResultsAsync(path);
    }

    private async void OnDumpFileList(object? sender, RoutedEventArgs e)
    {
        var file = await StorageProvider.SaveFilePickerAsync(new FilePickerSaveOptions
        {
            Title = "Save file list",
            SuggestedFileName = "filelist.txt",
            FileTypeChoices = new[]
            {
                new FilePickerFileType("Text Files") { Patterns = new[] { "*.txt" } }
            }
        });

        var path = file?.TryGetLocalPath();
        if (path != null)
            await VM.DumpFileListAsync(path);
    }

    private async Task<string?> PickOutputFolder()
    {
        var folders = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions
        {
            Title = "Select output folder for extraction",
            AllowMultiple = false
        });

        var folder = folders.FirstOrDefault();
        return folder?.TryGetLocalPath();
    }
}
