using System.Diagnostics;
using System.Text;
using System.Text.Json;
using ApiTestTool.Standards;
using ApiTestTool.Store;
using ApiTestTool.Store.Entities;
using Microsoft.Extensions.AI;

namespace ApiTestTool.AI.Rules;

public sealed record RuleDraftResult(string ClauseKey, IReadOnlyList<RuleEntity> Rules, string? Skipped, string Status, string? Error, long DurationMs);

/// <summary>
/// Drafts rules from one clause. The model proposes; code decides what reaches the review queue: the quote must occur in
/// the clause (<see cref="QuoteVerifier"/>) or the rule is stored as <c>invalid</c>; a level not backed by the quote's
/// wording and a near-duplicate of an existing rule are flagged for the reviewer. Every rule starts as <c>draft</c>.
/// </summary>
public sealed class RuleDrafter(AqtStore store, ChatClientFactory clients)
{
    /// <summary>Cosine distance under which two rules are flagged as possibly the same (bge-m3).</summary>
    internal const double DuplicateDistance = 0.12;

    internal const string SystemPrompt = """
        You turn one clause of an open banking standard into rules for an automated conformance test tool. The standards
        are the Berlin Group NextGenPSD2 XS2A Implementation Guidelines and the Georgian open banking guides (XS2A, JWS,
        identification/authentication), which profile NextGenPSD2. The tool tests a bank's (ASPSP's) XS2A API by acting
        as a TPP: mutual TLS with a QWAC, requests signed with a QSealC (Digest, X-Jws-Signature), OAuth2 with PKCE for
        SCA. It can send any request (add, remove or corrupt headers and body fields, wrong signatures), follow _links,
        complete SCA, and check status codes, error codes (tppMessages[].code), headers, JSON fields and signatures of
        responses. It cannot see the bank's internal processing.

        A rule is ONE requirement on the bank's API, stated in the clause, that someone can check:
        - Draft a rule for each normative statement about what the ASPSP's interface must, should or may do, including
          how it must react to wrong TPP requests (e.g. reject with 400 FORMAT_ERROR).
        - Do not draft rules for informative text, examples, history, definitions, duties of the TPP or PSU alone,
          or anything not in this clause. Do not combine two requirements into one rule; do not split one into several.
        - Do not repeat a rule listed under EXISTING RULES; if the clause adds nothing new, return no rules.
        - Fewer, precise rules are better than many vague ones. Return an empty list if the clause is not normative.

        Fields:
        - quote: the exact passage of the clause the rule rests on, copied character for character in the clause's own
          language (do not translate, fix typos or reformat). One contiguous passage of one to three sentences; if you
          must skip words in between, write "..." there. The tool checks the quote against the clause text and discards
          the rule if it is not found.
        - quoteEnglish: an English translation of the quote if the clause is not in English, otherwise "".
        - level: MUST (must, shall, required, mandatory, must not, not allowed; Georgian აუცილებელია, უნდა, სავალდებულო,
          დაუშვებელია), SHOULD (should, recommended; უმჯობესია, რეკომენდებულია), MAY (may, optional; შეიძლება,
          შესაძლებელია). Take it from the quote's wording, not from how important it seems.
          A function the text calls optional ("if this function is supported", "the ASPSP may offer") is MAY: the
          rule is that the bank MAY support it, plus, where the text says so, what it must do if it does or does not.
          Text that only describes how a request is built is not a rule that the bank must accept it.
        - requirement: one sentence in English, phrased as what the bank's API does, specific enough to test
          ("The ASPSP rejects a consent request whose validUntil is in the past with HTTP 400 and FORMAT_ERROR.").
        - title: at most 10 words.
        - service: AIS (accounts, consents, balances, transactions, card accounts), PIS (payments), COMMON (signatures,
          TLS, OAuth, headers and errors used by both).
        - area: CON consent, ACC accounts/balances, TRX transactions, PIS payment initiation, PST payment status,
          PCN payment cancellation, SBK signing baskets, AUTH OAuth/SCA, SIG signatures/certificates, TLS transport,
          ERR error responses, HDR headers, GEN other.
        - operation: the HTTP method and path the rule is about, e.g. "POST /v2/consents/account-access"; "" if it is
          not tied to one endpoint.
        - check: how a TPP-side test observes it. kind: negative (send a wrong request, expect rejection), positive
          (correct request, expect success/fields), header, signature, flow (multi-step), manual (not observable by an
          automated TPP test). expectStatus and expectErrorCodes only when the clause or its context states them;
          otherwise empty arrays. description: one sentence.
        - notes: ambiguity or assumptions, e.g. where the standard is unclear; "" if none.
        - skipped: one or two sentences on what in the clause you did not turn into rules and why.

        Reply with one JSON object only, matching this JSON schema:
        """ + RuleDraftAnswer.JsonSchema;

    public async Task<RuleDraftResult> DraftAsync(string clauseKey, string? triggeredBy = null, CancellationToken cancellationToken = default)
    {
        var sw = Stopwatch.StartNew();
        var clause = await store.GetClauseAsync(clauseKey, cancellationToken) ?? throw new InvalidOperationException($"Clause '{clauseKey}' is not in the store (aqt standards ingest)");
        if (string.IsNullOrWhiteSpace(clause.Text))
            return new RuleDraftResult(clauseKey, [], "The clause has no text of its own (only sub-sections).", "ok", null, sw.ElapsedMilliseconds);

        var ancestors = await store.GetAncestorsAsync(clause, cancellationToken);
        var existing = (await store.ListRulesAsync(clauseKey: clauseKey, cancellationToken: cancellationToken)).Where(r => r.Status is "draft" or "approved").ToList();
        var settings = clients.Describe(AiFeatures.RuleDrafting);

        List<ChatMessage> messages = [new(ChatRole.System, SystemPrompt), new(ChatRole.User, UserMessage(clause, ancestors, existing))];
        RuleDraftAnswer? answer = null;
        Guid callId = default;
        string? error = null;

        for (var attempt = 1; attempt <= 2 && answer is null; attempt++)
        {
            callId = Guid.NewGuid();
            var client = clients.Create(AiFeatures.RuleDrafting, new AiCallContext(null, triggeredBy, callId));
            var options = clients.ChatOptionsFor(AiFeatures.RuleDrafting);
            if (settings.Provider == AiProviders.Ollama)
                options.ResponseFormat = ChatResponseFormat.ForJsonSchema(JsonDocument.Parse(RuleDraftAnswer.JsonSchema).RootElement, "rule_drafts");

            ChatResponse response;
            try
            {
                response = await client.GetResponseAsync(messages, options, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
            {
                return new RuleDraftResult(clauseKey, [], null, "failed", $"{ex.GetType().Name}: {ex.GetBaseException().Message}", sw.ElapsedMilliseconds);
            }

            if (response.FinishReason == ChatFinishReason.ContentFilter)
                return new RuleDraftResult(clauseKey, [], null, "failed", "The model declined to answer (refusal).", sw.ElapsedMilliseconds);

            try
            {
                answer = RuleDraftAnswer.Parse(response.Text);
            }
            catch (JsonException ex)
            {
                error = $"Unreadable answer: {ex.Message}";
                messages.AddRange(response.Messages);
                messages.Add(new ChatMessage(ChatRole.User, $"Your answer could not be read as the required JSON ({ex.Message}). Reply again with only the JSON object."));
            }
        }

        if (answer is null)
            return new RuleDraftResult(clauseKey, [], null, "failed", error, sw.ElapsedMilliseconds);

        var rules = await CheckAndNumberAsync(answer.Rules, clause, settings, callId, cancellationToken);
        var now = DateTime.UtcNow;
        await store.AddRulesAsync(rules, [.. rules.Select(r => new RuleReviewEntity
        {
            RuleId = r.Id,
            TimeUtc = now,
            Reviewer = triggeredBy ?? "ai",
            Action = "drafted",
            Note = r.Status == "invalid" ? "Failed the automatic checks: " + string.Join(" ", r.CheckNotes) : null,
            After = JsonSerializer.Serialize(RuleSnapshot.Of(r), RuleDraftAnswer.Json),
        })], cancellationToken);

        return new RuleDraftResult(clauseKey, rules, answer.Skipped, "ok", null, sw.ElapsedMilliseconds);
    }

    internal static string UserMessage(ClauseEntity clause, IReadOnlyList<ClauseEntity> ancestors, IReadOnlyList<RuleEntity> existing)
    {
        var sb = new StringBuilder();
        sb.AppendLine($"STANDARD {clause.Standard} version {clause.Version} · language {(clause.Language == "en" ? "English" : "Georgian")}");
        if (ancestors.Count > 0)
            sb.AppendLine("SECTION PATH: " + string.Join(" › ", ancestors.Select(a => $"{a.Number} {a.Title}")));
        sb.AppendLine($"CLAUSE {clause.Number} {clause.Title}{(clause.Page is { } p ? $" (page {p})" : "")}");
        sb.AppendLine();
        sb.AppendLine("CLAUSE TEXT:");
        sb.AppendLine(clause.Text);
        sb.AppendLine();
        sb.AppendLine(existing.Count == 0 ? "EXISTING RULES: none" : "EXISTING RULES (do not repeat):");
        foreach (var r in existing)
            sb.AppendLine($"- {r.Id} [{r.Status}] {r.Level}: {r.Requirement}");
        return sb.ToString();
    }

    private async Task<List<RuleEntity>> CheckAndNumberAsync(List<RuleDraft> drafts, ClauseEntity clause, AiFeatureOptions settings, Guid callId, CancellationToken cancellationToken)
    {
        var prefix = IdPrefix(clause.Standard);
        var next = new Dictionary<string, int>();
        var now = DateTime.UtcNow;
        IEmbeddingGenerator<string, Embedding<float>>? embeddings = clients.CreateEmbeddingGenerator();
        var result = new List<RuleEntity>();

        foreach (var d in drafts)
        {
            var notes = new List<string>();
            var level = Pick(d.Level.ToUpperInvariant(), ["MUST", "SHOULD", "MAY"], "MUST", "level", notes);
            var service = Pick(d.Service.ToUpperInvariant(), ["AIS", "PIS", "COMMON"], "COMMON", "service", notes);
            var area = Pick(d.Area.ToUpperInvariant(), ["CON", "ACC", "TRX", "PIS", "PST", "PCN", "SBK", "AUTH", "SIG", "TLS", "ERR", "HDR", "GEN"], "GEN", "area", notes);

            var quoteProblem = QuoteVerifier.Check(d.Quote, clause.Text);
            var invalid = quoteProblem is not null || string.IsNullOrWhiteSpace(d.Requirement);
            notes.Add(quoteProblem is null ? "Quote found in the clause." : "Quote check failed: " + quoteProblem);
            if (string.IsNullOrWhiteSpace(d.Requirement))
                notes.Add("No requirement text.");
            if (quoteProblem is null && NormativeLevel.Question(level, d.Quote) is { } levelNote)
                notes.Add(levelNote);
            if (clause.Language != "en" && string.IsNullOrWhiteSpace(d.QuoteEnglish))
                notes.Add("No English translation of the quote.");

            var idPrefix = $"{prefix}-{area}";
            if (!next.TryGetValue(idPrefix, out var number))
                number = await store.MaxRuleNumberAsync(idPrefix, cancellationToken);
            next[idPrefix] = ++number;

            var rule = new RuleEntity
            {
                Id = $"{idPrefix}-{number:000}",
                Status = invalid ? "invalid" : "draft",
                Title = d.Title.Trim(),
                Level = level,
                Service = service,
                Area = area,
                Requirement = d.Requirement.Trim(),
                Standard = clause.Standard,
                Version = clause.Version,
                ClauseNumber = clause.Number,
                ClauseKey = clause.Key,
                Quote = d.Quote.Trim(),
                QuoteEnglish = clause.Language == "en" || string.IsNullOrWhiteSpace(d.QuoteEnglish) ? null : d.QuoteEnglish.Trim(),
                Operation = string.IsNullOrWhiteSpace(d.Operation) ? null : d.Operation.Trim(),
                CheckJson = JsonSerializer.Serialize(d.Check, RuleDraftAnswer.Json),
                Notes = string.IsNullOrWhiteSpace(d.Notes) ? null : d.Notes.Trim(),
                DraftedBy = "ai",
                Model = $"{settings.Provider}:{settings.Model}",
                AiCallId = callId,
                CreatedUtc = now,
                UpdatedUtc = now,
            };

            if (!invalid && embeddings is not null)
            {
                try
                {
                    var vector = (await embeddings.GenerateAsync([rule.Requirement], cancellationToken: cancellationToken))[0].Vector.ToArray();
                    rule.Embedding = new Pgvector.Vector(vector);

                    // Close in meaning AND about the same things: embeddings alone rate "accept availableAccounts" and
                    // "accept allPsd2" as near-identical, so identifiers (fields, codes, statuses, paths) must match too.
                    var candidates = (await store.FindSimilarRulesAsync(vector, DuplicateDistance, cancellationToken))
                        .Concat(result.Where(r => r.Embedding is not null).Select(r => (Rule: r, Distance: CosineDistance(vector, r.Embedding!.ToArray()))))
                        .Where(x => x.Distance <= DuplicateDistance && SameIdentifiers(rule.Requirement, x.Rule.Requirement))
                        .OrderBy(x => x.Distance);
                    var best = candidates.FirstOrDefault();
                    if (best.Rule is not null)
                    {
                        rule.SimilarTo = best.Rule.Id;
                        notes.Add($"Possible duplicate of {best.Rule.Id} ({best.Rule.Status}, similarity {1 - best.Distance:0.00}): {best.Rule.Requirement}");
                    }
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException && !cancellationToken.IsCancellationRequested)
                {
                    embeddings = null; // Ollama not available: skip the duplicate check for the rest, say so once
                    notes.Add($"Duplicate check skipped: local embedding model unavailable ({ex.GetBaseException().Message}).");
                }
            }

            rule.CheckNotes = notes;
            result.Add(rule);
        }

        return result;
    }

    /// <summary>Identifiers a requirement names: quoted terms, camelCase fields, UPPER_CASE codes, HTTP statuses, paths.</summary>
    internal static IReadOnlySet<string> Identifiers(string text) =>
        System.Text.RegularExpressions.Regex.Matches(text, @"""[^""]+""|'[^']+'|\b[a-z]+[A-Z]\w*\b|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b[1-5]\d\d\b|/[\w/{}.-]+")
            .Select(m => m.Value.Trim('"', '\'').ToLowerInvariant())
            .ToHashSet();

    internal static bool SameIdentifiers(string a, string b) => Identifiers(a).SetEquals(Identifiers(b));

    /// <summary>Rule id prefix per standard family: BG-IG → BG, GE-XS2A/GE-JWS/GE-IAA → GE, NBG-80-04 → NBG.</summary>
    internal static string IdPrefix(string standard) => standard.Split('-')[0].ToUpperInvariant();

    private static string Pick(string value, string[] allowed, string fallback, string what, List<string> notes)
    {
        if (allowed.Contains(value))
            return value;
        notes.Add($"Unknown {what} \"{value}\" replaced by {fallback}.");
        return fallback;
    }

    internal static double CosineDistance(float[] a, float[] b)
    {
        double dot = 0, na = 0, nb = 0;
        for (var i = 0; i < a.Length; i++)
        {
            dot += a[i] * b[i];
            na += a[i] * a[i];
            nb += b[i] * b[i];
        }

        return 1 - dot / (Math.Sqrt(na) * Math.Sqrt(nb));
    }
}

/// <summary>The reviewable fields of a rule, as stored in <c>rule_reviews.before/after</c>.</summary>
public sealed record RuleSnapshot(string Status, string Title, string Level, string Service, string Area, string Requirement, string Quote, string? QuoteEnglish, string? Operation, string? CheckJson)
{
    public static RuleSnapshot Of(RuleEntity r) => new(r.Status, r.Title, r.Level, r.Service, r.Area, r.Requirement, r.Quote, r.QuoteEnglish, r.Operation, r.CheckJson);
}
