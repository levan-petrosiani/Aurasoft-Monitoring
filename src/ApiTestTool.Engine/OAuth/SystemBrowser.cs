using System.Diagnostics;
using System.Runtime.InteropServices;

namespace ApiTestTool.Engine.OAuth;

internal static class SystemBrowser
{
    /// <summary>Opens the URL in the default browser. If that fails, the tester can still copy the URL from the console.</summary>
    public static Task OpenAsync(string url, CancellationToken cancellationToken)
    {
        try
        {
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            else if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
                Process.Start("open", url);
            else
                Process.Start("xdg-open", url);
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            // Not fatal: the URL is also shown in the console.
        }

        return Task.CompletedTask;
    }
}
