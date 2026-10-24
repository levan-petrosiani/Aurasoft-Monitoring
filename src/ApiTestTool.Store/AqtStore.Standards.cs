using System.Globalization;
using System.Text;
using ApiTestTool.Store.Entities;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace ApiTestTool.Store;

/// <summary>A clause found by <see cref="AqtStore.SearchClausesAsync"/>, with how it matched.</summary>
public sealed record ClauseHit(ClauseEntity Clause, double Score, bool ByWords, bool ByMeaning);

/// <summary>Standards, clauses and rules.</summary>
public sealed partial class AqtStore
{
    /// <summary>Replaces a document's clauses (rules reference clauses by key, so they survive a re-ingest).</summary>
    public async Task ReplaceStandardAsync(StandardDocumentEntity document, IReadOnlyList<ClauseEntity> clauses, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        var strategy = db.Database.CreateExecutionStrategy();
        await strategy.ExecuteAsync(async () =>
        {
            await using var tx = await db.Database.BeginTransactionAsync(cancellationToken);
            await db.Clauses.Where(c => c.Standard == document.Id && c.Version == document.Version).ExecuteDeleteAsync(cancellationToken);
            await db.StandardDocuments.Where(d => d.Id == document.Id && d.Version == document.Version).ExecuteDeleteAsync(cancellationToken);
            db.StandardDocuments.Add(document);
            db.Clauses.AddRange(clauses);
            await db.SaveChangesAsync(cancellationToken);
            await tx.CommitAsync(cancellationToken);
        });
    }

    public async Task SetClauseEmbeddingsAsync(IReadOnlyList<(long Id, float[] Vector)> embeddings, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        foreach (var (id, vector) in embeddings)
            await db.Clauses.Where(c => c.Id == id).ExecuteUpdateAsync(s => s.SetProperty(c => c.Embedding, new Pgvector.Vector(vector)), cancellationToken);
    }

    public async Task<List<StandardDocumentEntity>> ListStandardsAsync(CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.StandardDocuments.AsNoTracking().OrderBy(d => d.Id).ThenBy(d => d.Version).ToListAsync(cancellationToken);
    }

    /// <summary>Clauses of a document in reading order, without text and embeddings (for outlines).</summary>
    public async Task<List<ClauseEntity>> GetOutlineAsync(string standard, string version, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.Clauses.AsNoTracking()
            .Where(c => c.Standard == standard && c.Version == version)
            .OrderBy(c => c.Ord)
            .Select(c => new ClauseEntity
            {
                Id = c.Id, Standard = c.Standard, Version = c.Version, Number = c.Number, Title = c.Title, Text = "", Level = c.Level,
                Parent = c.Parent, Ord = c.Ord, Page = c.Page, Language = c.Language, Key = c.Key,
            })
            .ToListAsync(cancellationToken);
    }

    public async Task<ClauseEntity?> GetClauseAsync(string key, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.Clauses.AsNoTracking().SingleOrDefaultAsync(c => c.Key == key, cancellationToken);
    }

    /// <summary>Titles of the enclosing sections, outermost first (e.g. "6 Account Information Service › 6.3 ...").</summary>
    public async Task<List<ClauseEntity>> GetAncestorsAsync(ClauseEntity clause, CancellationToken cancellationToken = default)
    {
        var numbers = new List<string>();
        for (var n = clause.Parent; n is not null; n = n.LastIndexOf('.') is var i and > 0 ? n[..i] : null)
            numbers.Add(n);

        await using var db = CreateContext();
        var found = await db.Clauses.AsNoTracking()
            .Where(c => c.Standard == clause.Standard && c.Version == clause.Version && numbers.Contains(c.Number))
            .ToListAsync(cancellationToken);
        return [.. found.OrderBy(c => c.Level)];
    }

    /// <summary>
    /// Hybrid search: full-text (English stemming for English documents, plain words otherwise) and, when a query
    /// embedding is given, meaning (cosine distance on bge-m3 vectors). Results are merged by reciprocal rank.
    /// </summary>
    public async Task<List<ClauseHit>> SearchClausesAsync(string query, float[]? embedding, string? standard = null, int limit = 10, CancellationToken cancellationToken = default)
    {
        const string sql = """
            WITH q AS (SELECT websearch_to_tsquery('english', @q) || websearch_to_tsquery('simple', @q) AS tsq),
            fts AS (
                SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.search, q.tsq) DESC) AS r
                FROM clauses c, q
                WHERE c.search @@ q.tsq AND (@std::text IS NULL OR c.standard = @std)
                LIMIT 50),
            vec AS (
                SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> @v::vector) AS r
                FROM clauses c
                WHERE @v::text IS NOT NULL AND c.embedding IS NOT NULL AND (@std::text IS NULL OR c.standard = @std)
                ORDER BY c.embedding <=> @v::vector
                LIMIT 50)
            SELECT c.id, COALESCE(1.0 / (60 + fts.r), 0) + COALESCE(1.0 / (60 + vec.r), 0) AS score, fts.id IS NOT NULL, vec.id IS NOT NULL
            FROM clauses c
            LEFT JOIN fts ON fts.id = c.id
            LEFT JOIN vec ON vec.id = c.id
            WHERE fts.id IS NOT NULL OR vec.id IS NOT NULL
            ORDER BY score DESC
            LIMIT @n
            """;

        await using var db = CreateContext();
        var connection = (NpgsqlConnection)db.Database.GetDbConnection();
        await connection.OpenAsync(cancellationToken);
        await using var cmd = new NpgsqlCommand(sql, connection);
        cmd.Parameters.AddWithValue("q", AnyWord(query));
        cmd.Parameters.Add(new NpgsqlParameter("std", NpgsqlTypes.NpgsqlDbType.Text) { Value = (object?)standard ?? DBNull.Value });
        cmd.Parameters.Add(new NpgsqlParameter("v", NpgsqlTypes.NpgsqlDbType.Text) { Value = embedding is null ? DBNull.Value : VectorLiteral(embedding) });
        cmd.Parameters.AddWithValue("n", limit);

        var ranked = new List<(long Id, double Score, bool Words, bool Meaning)>();
        await using (var reader = await cmd.ExecuteReaderAsync(cancellationToken))
        {
            while (await reader.ReadAsync(cancellationToken))
                ranked.Add((reader.GetInt64(0), reader.GetDouble(1), reader.GetBoolean(2), reader.GetBoolean(3)));
        }

        var ids = ranked.Select(r => r.Id).ToList();
        var clauses = await db.Clauses.AsNoTracking().Where(c => ids.Contains(c.Id)).ToDictionaryAsync(c => c.Id, cancellationToken);
        return [.. ranked.Select(r => new ClauseHit(clauses[r.Id], r.Score, r.Words, r.Meaning))];
    }

    // ---- rules ----

    public async Task<RuleEntity?> GetRuleAsync(string id, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.Rules.AsNoTracking().SingleOrDefaultAsync(r => r.Id == id, cancellationToken);
    }

    public async Task<List<RuleEntity>> ListRulesAsync(string? status = null, string? standard = null, string? clauseKey = null, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.Rules.AsNoTracking()
            .Where(r => (status == null || r.Status == status) && (standard == null || r.Standard == standard) && (clauseKey == null || r.ClauseKey == clauseKey))
            .OrderBy(r => r.Standard).ThenBy(r => r.ClauseKey).ThenBy(r => r.Id)
            .ToListAsync(cancellationToken);
    }

    public async Task<List<RuleReviewEntity>> GetRuleHistoryAsync(string ruleId, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.RuleReviews.AsNoTracking().Where(r => r.RuleId == ruleId).OrderBy(r => r.TimeUtc).ToListAsync(cancellationToken);
    }

    /// <summary>Highest number used so far for ids like <c>BG-CON-007</c> (prefix "BG-CON").</summary>
    public async Task<int> MaxRuleNumberAsync(string prefix, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        var ids = await db.Rules.AsNoTracking().Where(r => r.Id.StartsWith(prefix + "-")).Select(r => r.Id).ToListAsync(cancellationToken);
        return ids.Select(id => int.TryParse(id[(prefix.Length + 1)..], NumberStyles.None, CultureInfo.InvariantCulture, out var n) ? n : 0).DefaultIfEmpty(0).Max();
    }

    /// <summary>Rules (not rejected/invalid/retired) whose embedding is within <paramref name="maxDistance"/> (cosine) of the vector.</summary>
    public async Task<List<(RuleEntity Rule, double Distance)>> FindSimilarRulesAsync(float[] vector, double maxDistance, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        var connection = (NpgsqlConnection)db.Database.GetDbConnection();
        await connection.OpenAsync(cancellationToken);
        await using var cmd = new NpgsqlCommand("""
            SELECT id, embedding <=> @v::vector AS d FROM rules
            WHERE embedding IS NOT NULL AND status IN ('draft', 'approved') AND embedding <=> @v::vector <= @max
            ORDER BY d LIMIT 5
            """, connection);
        cmd.Parameters.AddWithValue("v", VectorLiteral(vector));
        cmd.Parameters.AddWithValue("max", maxDistance);

        var found = new List<(string Id, double Distance)>();
        await using (var reader = await cmd.ExecuteReaderAsync(cancellationToken))
        {
            while (await reader.ReadAsync(cancellationToken))
                found.Add((reader.GetString(0), reader.GetDouble(1)));
        }

        var ids = found.Select(f => f.Id).ToList();
        var rules = await db.Rules.AsNoTracking().Where(r => ids.Contains(r.Id)).ToDictionaryAsync(r => r.Id, cancellationToken);
        return [.. found.Select(f => (rules[f.Id], f.Distance))];
    }

    /// <summary>Adds new rules and their "drafted" history rows in one transaction.</summary>
    public async Task AddRulesAsync(IReadOnlyList<RuleEntity> rules, IReadOnlyList<RuleReviewEntity> history, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        db.Rules.AddRange(rules);
        db.RuleReviews.AddRange(history);
        await db.SaveChangesAsync(cancellationToken);
    }

    /// <summary>Saves a changed rule and its history row. Fails if someone else changed the rule since it was read.</summary>
    public async Task UpdateRuleAsync(RuleEntity rule, DateTime expectedUpdatedUtc, RuleReviewEntity history, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        var current = await db.Rules.SingleOrDefaultAsync(r => r.Id == rule.Id, cancellationToken) ?? throw new InvalidOperationException($"Rule {rule.Id} no longer exists");
        if (current.UpdatedUtc != expectedUpdatedUtc)
            throw new InvalidOperationException($"Rule {rule.Id} was changed by someone else ({current.ReviewedBy ?? current.DraftedBy}); reload it and try again.");

        db.Entry(current).CurrentValues.SetValues(rule);
        db.RuleReviews.Add(history);
        await db.SaveChangesAsync(cancellationToken);
    }

    /// <summary>
    /// websearch_to_tsquery needs every word by default, which is too strict for questions; join the words with "or"
    /// (ranking still prefers clauses with more of them). Quoted phrases and "-word" keep their meaning.
    /// </summary>
    internal static string AnyWord(string query)
    {
        if (query.Contains('"', StringComparison.Ordinal) || query.Contains(" or ", StringComparison.OrdinalIgnoreCase))
            return query;
        return string.Join(" or ", query.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));
    }

    private static string VectorLiteral(float[] v)
    {
        var sb = new StringBuilder("[");
        for (var i = 0; i < v.Length; i++)
        {
            if (i > 0)
                sb.Append(',');
            sb.Append(v[i].ToString("R", CultureInfo.InvariantCulture));
        }

        return sb.Append(']').ToString();
    }
}
