using System.Diagnostics;
using ApiTestTool.Standards;
using ApiTestTool.Store;
using ApiTestTool.Store.Entities;
using Microsoft.Extensions.AI;

namespace ApiTestTool.AI.Standards;

public sealed record IngestResult(string Standard, string Version, int Clauses, int Embedded, IReadOnlyList<string> Problems, long DurationMs);

/// <summary>
/// Loads the documents listed in <c>standards/sources.yaml</c> into the store as clauses, and (optionally) adds local
/// bge-m3 embeddings for search by meaning. Embedding failures do not stop the ingest: full-text search still works.
/// </summary>
public sealed class StandardsIngestor(AqtStore store, StandardSources sources, ChatClientFactory clients)
{
    /// <summary>bge-m3 reads up to 8192 tokens; Georgian text needs about one token per character, so stay well below.</summary>
    internal const int MaxEmbeddingChars = 4000;

    private const int EmbeddingBatch = 16;

    public async Task<IngestResult> IngestAsync(StandardSource source, bool embed, Action<string>? progress = null, CancellationToken cancellationToken = default)
    {
        var sw = Stopwatch.StartNew();
        var (clauses, problems) = sources.Read(source);

        var entities = clauses.Select(c => new ClauseEntity
        {
            Standard = c.Standard,
            Version = c.Version,
            Number = c.Number,
            Title = c.Title,
            Text = c.Text,
            Level = c.Level,
            Parent = c.Parent,
            Ord = c.Order,
            Page = c.Page,
            Language = c.Language,
            Key = c.Key,
        }).ToList();

        await store.ReplaceStandardAsync(new StandardDocumentEntity
        {
            Id = source.Id,
            Version = source.Version,
            Title = source.Title,
            Publisher = source.Publisher,
            Language = source.Language,
            Url = source.Url,
            Sha256 = source.Sha256,
            License = source.License,
            IngestedUtc = DateTime.UtcNow,
            ClauseCount = entities.Count,
        }, entities, cancellationToken);
        progress?.Invoke($"{source.Id} {source.Version}: {entities.Count} clauses stored");

        var embedded = 0;
        if (embed)
        {
            try
            {
                embedded = await EmbedAsync(entities, progress, cancellationToken);
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException && !cancellationToken.IsCancellationRequested)
            {
                problems.Add($"Embeddings skipped ({ex.GetBaseException().Message}); full-text search still works. Is Ollama running with {clients.Options.Ollama.EmbeddingModel}?");
            }
        }

        return new IngestResult(source.Id, source.Version, entities.Count, embedded, problems, sw.ElapsedMilliseconds);
    }

    private async Task<int> EmbedAsync(List<ClauseEntity> clauses, Action<string>? progress, CancellationToken cancellationToken)
    {
        var generator = clients.CreateEmbeddingGenerator();
        var done = 0;
        foreach (var batch in clauses.Chunk(EmbeddingBatch))
        {
            var inputs = batch.Select(EmbeddingText).ToList();
            var vectors = await generator.GenerateAsync(inputs, cancellationToken: cancellationToken);
            await store.SetClauseEmbeddingsAsync([.. batch.Zip(vectors).Select(p => (p.First.Id, p.Second.Vector.ToArray()))], cancellationToken);
            done += batch.Length;
            progress?.Invoke($"  embedded {done}/{clauses.Count}");
        }

        return done;
    }

    /// <summary>What a clause is searched by: number, title and the start of its text.</summary>
    internal static string EmbeddingText(ClauseEntity c)
    {
        var text = $"{c.Number} {c.Title}\n{c.Text}";
        return text.Length <= MaxEmbeddingChars ? text : text[..MaxEmbeddingChars];
    }
}
