using ApiTestTool.Store;
using ApiTestTool.Store.Entities;

namespace ApiTestTool.AI;

/// <summary>Where audit records of model calls go.</summary>
public interface IAiAuditSink
{
    Task RecordAsync(AiCallEntity call);

    /// <summary>Called when <see cref="RecordAsync"/> threw; must not throw itself.</summary>
    void ReportFailure(AiCallEntity call, Exception error);
}

/// <summary>Writes audit records to the <c>ai_calls</c> table.</summary>
public sealed class StoreAuditSink(AqtStore store, Action<string>? warn = null) : IAiAuditSink
{
    public Task RecordAsync(AiCallEntity call) => store.AddAiCallAsync(call);

    public void ReportFailure(AiCallEntity call, Exception error) =>
        warn?.Invoke($"AI audit record {call.Id} ({call.Feature}) could not be saved: {error.GetBaseException().Message}");
}

/// <summary>No database configured: keeps the records in memory for the current process (CLI one-offs, tests).</summary>
public sealed class InMemoryAuditSink : IAiAuditSink
{
    private readonly List<AiCallEntity> _calls = [];

    public IReadOnlyList<AiCallEntity> Calls
    {
        get { lock (_calls) return [.. _calls]; }
    }

    public Task RecordAsync(AiCallEntity call)
    {
        lock (_calls)
            _calls.Add(call);
        return Task.CompletedTask;
    }

    public void ReportFailure(AiCallEntity call, Exception error)
    {
    }
}
