using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ApiTestTool.Store.Entities;
using ApiTestTool.Store.Masking;
using Microsoft.Extensions.AI;

namespace ApiTestTool.AI;

/// <summary>Who/what a model call is for; stored with the audit record. <paramref name="CallId"/> lets a feature link its own rows to the audit row.</summary>
public sealed record AiCallContext(string? RunId = null, string? TriggeredBy = null, Guid? CallId = null);

/// <summary>
/// Wraps every provider client: masks the text of outgoing messages (last line of defence — features mask their
/// inputs first) and writes one <see cref="AiCallEntity"/> per call, successful or not.
/// </summary>
public sealed class AuditingChatClient(IChatClient inner, string feature, string provider, string model, IAiAuditSink audit, SensitiveDataMasker masker, AiCallContext? context = null)
    : DelegatingChatClient(inner)
{
    private static readonly JsonSerializerOptions s_json = new() { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    public override async Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default)
    {
        var masked = Mask(messages);
        var sw = Stopwatch.StartNew();
        try
        {
            var response = await base.GetResponseAsync(masked, options, cancellationToken);
            await RecordAsync(masked, response, null, sw.ElapsedMilliseconds);
            return response;
        }
        catch (Exception ex) when (ex is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
        {
            // Timeouts surface as OperationCanceledException too; only the caller's own cancellation goes unrecorded.
            await RecordAsync(masked, null, ex, sw.ElapsedMilliseconds);
            throw;
        }
    }

    public override async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        var masked = Mask(messages);
        var sw = Stopwatch.StartNew();
        var updates = new List<ChatResponseUpdate>();

        await using var e = base.GetStreamingResponseAsync(masked, options, cancellationToken).GetAsyncEnumerator(cancellationToken);
        while (true)
        {
            ChatResponseUpdate update;
            try
            {
                if (!await e.MoveNextAsync())
                    break;
                update = e.Current;
            }
            catch (Exception ex) when (ex is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
            {
                await RecordAsync(masked, null, ex, sw.ElapsedMilliseconds);
                throw;
            }

            updates.Add(update);
            yield return update;
        }

        await RecordAsync(masked, updates.ToChatResponse(), null, sw.ElapsedMilliseconds);
    }

    private List<ChatMessage> Mask(IEnumerable<ChatMessage> messages) =>
        [.. messages.Select(m => new ChatMessage(m.Role, [.. m.Contents.Select(c => c is TextContent t ? new TextContent(masker.MaskBody(t.Text)) : c)]))];

    private async Task RecordAsync(List<ChatMessage> messages, ChatResponse? response, Exception? error, long durationMs)
    {
        var request = JsonSerializer.Serialize(messages.Select(m => new { role = m.Role.Value, text = m.Text }), s_json);
        var call = new AiCallEntity
        {
            Id = context?.CallId ?? Guid.NewGuid(),
            TimeUtc = DateTime.UtcNow,
            Feature = feature,
            Provider = provider,
            Model = response?.ModelId ?? model,
            RunId = context?.RunId,
            TriggeredBy = context?.TriggeredBy ?? Environment.UserName,
            PromptSha256 = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(request))),
            Request = request,
            Response = response?.Text,
            InputTokens = response?.Usage?.InputTokenCount,
            OutputTokens = response?.Usage?.OutputTokenCount,
            DurationMs = durationMs,
            Succeeded = error is null,
            Error = error is null ? null : $"{error.GetType().Name}: {error.Message}",
        };

        try
        {
            await audit.RecordAsync(call);
        }
        catch (Exception ex)
        {
            // Never lose the answer because the audit write failed; the sink decides how loudly to complain.
            audit.ReportFailure(call, ex);
        }
    }
}
