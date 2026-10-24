using System.Security.Cryptography.X509Certificates;
using ApiTestTool.Engine.Profiles;

namespace ApiTestTool.Engine.Certificates;

public static class CertificateLoader
{
    /// <summary>
    /// Loads a certificate with its private key from a PFX file or the Windows certificate store, or creates a self-signed one.
    /// </summary>
    /// <param name="settings">Certificate settings from the bank profile.</param>
    /// <param name="baseDirectory">Directory that relative file paths are resolved against (the profile's folder).</param>
    /// <param name="name">Name used in error messages, e.g. "tpp.qwac".</param>
    public static X509Certificate2 Load(CertificateSettings settings, string baseDirectory, string name)
    {
        if (settings.SelfSigned is { } selfSigned)
            return DevCertificates.CreateTppCertificate(selfSigned.CommonName, selfSigned.OrganizationId);

        if (!string.IsNullOrWhiteSpace(settings.File))
            return LoadFromFile(settings, baseDirectory, name);

        if (!string.IsNullOrWhiteSpace(settings.Thumbprint))
            return LoadFromStore(settings, name);

        throw new ConfigurationException($"{name}: one of 'file', 'thumbprint' or 'selfSigned' must be specified");
    }

    private static X509Certificate2 LoadFromFile(CertificateSettings settings, string baseDirectory, string name)
    {
        var path = Path.IsPathRooted(settings.File!) ? settings.File! : Path.GetFullPath(Path.Combine(baseDirectory, settings.File!));
        if (!File.Exists(path))
            throw new ConfigurationException($"{name}: certificate file not found: {path}");

        var password = settings.Password;
        if (!string.IsNullOrWhiteSpace(settings.PasswordEnv))
        {
            password = Environment.GetEnvironmentVariable(settings.PasswordEnv)
                       ?? throw new ConfigurationException($"{name}: environment variable '{settings.PasswordEnv}' with the certificate password is not set");
        }

        try
        {
            // Not EphemeralKeySet: SChannel (Windows TLS) cannot use ephemeral keys for client authentication.
            var cert = X509CertificateLoader.LoadPkcs12FromFile(path, password, X509KeyStorageFlags.DefaultKeySet);
            if (!cert.HasPrivateKey)
                throw new ConfigurationException($"{name}: certificate '{path}' has no private key");
            return cert;
        }
        catch (System.Security.Cryptography.CryptographicException ex)
        {
            throw new ConfigurationException($"{name}: cannot open '{path}': {ex.Message}", ex);
        }
    }

    private static X509Certificate2 LoadFromStore(CertificateSettings settings, string name)
    {
        var (location, storeName) = ParseStore(settings.Store ?? "CurrentUser/My", name);
        var thumbprint = settings.Thumbprint!.Replace(" ", string.Empty, StringComparison.Ordinal);

        using var store = new X509Store(storeName, location);
        store.Open(OpenFlags.ReadOnly);

        var found = store.Certificates.Find(X509FindType.FindByThumbprint, thumbprint, validOnly: false);
        if (found.Count == 0)
            throw new ConfigurationException($"{name}: certificate with thumbprint {thumbprint} not found in {location}/{storeName}");

        var cert = found[0];
        if (!cert.HasPrivateKey)
            throw new ConfigurationException($"{name}: certificate {thumbprint} has no private key (or no access to it)");

        return cert;
    }

    private static (StoreLocation, StoreName) ParseStore(string value, string name)
    {
        var parts = value.Split('/', 2);
        if (parts.Length != 2
            || !Enum.TryParse<StoreLocation>(parts[0], ignoreCase: true, out var location)
            || !Enum.TryParse<StoreName>(parts[1], ignoreCase: true, out var storeName))
        {
            throw new ConfigurationException($"{name}: invalid store '{value}'. Expected e.g. 'CurrentUser/My' or 'LocalMachine/My'");
        }

        return (location, storeName);
    }
}
