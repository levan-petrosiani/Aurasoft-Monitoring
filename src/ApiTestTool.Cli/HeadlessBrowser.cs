using ApiTestTool.Engine.OAuth;

namespace ApiTestTool.Cli;

/// <summary><c>--sca-auto</c>: SCA without a browser, for auto-approving test servers such as MockBank.</summary>
internal static class HeadlessBrowser
{
    public static readonly Func<string, CancellationToken, Task> OpenAsync = HeadlessAuthorizer.Create(message => Out.Error(message));
}
