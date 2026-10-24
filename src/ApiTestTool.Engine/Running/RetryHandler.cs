using ApiTestTool.Engine.Profiles;

namespace ApiTestTool.Engine.Running;

/// <summary>
/// Retries transient failures (configured statuses, connection errors, per-attempt timeouts) and marks the final
/// response with <see cref="RetriedHeader"/> so the step can report the retries as warnings.
/// </summary>
internal sealed class RetryHandler(RetrySettings settings) : DelegatingHandler
{
    /// <summary>Added to responses that needed retries, e.g. "503, 503". Never sent to the bank.</summary>
    public const string RetriedHeader = "X-AQT-Retried";

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        // Make the body re-readable for every attempt.
        if (request.Content is not null)
            await request.Content.LoadIntoBufferAsync(cancellationToken).ConfigureAwait(false);

        var reasons = new List<string>();
        for (var attempt = 1; ; attempt++)
        {
            var last = attempt >= Math.Max(1, settings.Attempts);
            try
            {
                var response = await base.SendAsync(request, cancellationToken).ConfigureAwait(false);
                if (last || !settings.Statuses.Contains((int)response.StatusCode))
                {
                    if (reasons.Count > 0)
                        response.Headers.TryAddWithoutValidation(RetriedHeader, string.Join(", ", reasons));
                    return response;
                }

                reasons.Add(((int)response.StatusCode).ToString(System.Globalization.CultureInfo.InvariantCulture));
                response.Dispose();
            }
            catch (HttpRequestException ex) when (!last)
            {
                reasons.Add(ex.HttpRequestError.ToString());
            }

            await Task.Delay(settings.DelayMs * attempt, cancellationToken).ConfigureAwait(false);
        }
    }
}
