namespace ApiTestTool.Engine;

/// <summary>
/// A problem with the tool's own input (profile, scenario, certificate, variable) rather than with the bank's API.
/// Scenarios that hit it end with outcome <c>Error</c>, never <c>Failed</c>.
/// </summary>
public sealed class ConfigurationException : Exception
{
    public ConfigurationException(string message) : base(message)
    {
    }

    public ConfigurationException(string message, Exception innerException) : base(message, innerException)
    {
    }
}
