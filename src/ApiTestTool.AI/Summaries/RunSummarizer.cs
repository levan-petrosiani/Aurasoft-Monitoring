using System.Diagnostics;
using System.Text.Json;
using ApiTestTool.Store;
using ApiTestTool.Store.Entities;
using Microsoft.Extensions.AI;

namespace ApiTestTool.AI.Summaries;

/// <summary>Outcome of summarizing one run: the stored row plus the parsed summary when it was valid.</summary>
public sealed record RunSummaryResult(RunSummaryEntity Entity, RunSummary? Summary);

/// <summary>
/// Turns a stored run into a triage for testers: problems grouped by root cause, bank vs our side, evidence,
/// clause, next step, and what changed since the bank's previous run. Reads masked data from the store, checks the
/// answer in code (<see cref="RunSummary.Validate"/>) and stores the result in <c>run_summaries</c>.
/// </summary>
public sealed class RunSummarizer(AqtStore store, ChatClientFactory clients)
{
    internal const string SystemPrompt = """
        You triage results of an automated conformance test run against a bank's XS2A (Berlin Group NextGenPSD2 /
        Georgian open banking) sandbox. The test tool acts as a TPP: mutual TLS with a QWAC, requests signed with a
        QSealC (Digest + X-Jws-Signature), OAuth2 with PKCE for SCA, and each response checked against the standard.
        The readers are testers and bank integration engineers who will act on your summary, so be specific and brief.

        How to read the outcomes:
        - Failed: the bank's API did not behave as the scenario expects (a bank-side deviation, unless the evidence
          shows the scenario's own expectation is wrong).
        - Error: a problem on our side (configuration, certificates, test data, timeout waiting for a tester's login,
          network to localhost), not the bank.
        - Blocked: not executed because a critical preflight scenario failed; explain the preflight cause once.
        - Skipped: the bank profile does not declare the feature. This is expected and never a problem.
        - Warnings in passed scenarios are deviations that did not fail the test; mention them only if they matter
          for integration.

        Write the triage:
        - Group problems by root cause: one finding per cause, listing every scenario it explains. Several scenarios
          failing for one reason is one finding. Every Failed or Error scenario must appear in some finding.
        - side: "bank" (the bank's behaviour deviates), "our-side" (tool, profile, certificates, test data),
          "environment" (sandbox down, TLS/network, random 5xx), or "unclear" when the evidence does not decide it.
          Say "unclear" rather than guess. Two common cases:
          - HTTP 502/503/504 or timeouts are "environment" (sandbox unavailable), not a standards deviation, unless
            the same endpoint fails this way in the previous run too, which suggests a persistent bank problem.
          - An SCA step that fails with access_denied ("the PSU rejected") usually means the person logging in
            clicked Deny or chose nothing; use "unclear" and suggest re-running with an approved login.
        - Findings are only for Failed, Error and Blocked scenarios. Warnings from passed scenarios go in "attention".
        - evidence: a short quote from the given failures, status codes or response bodies. Use only the data given.
        - specRef: copy a clause reference only if it appears in the data (the scenario's "Spec:" line); otherwise "".
          Never cite a clause from memory.
        - nextStep: one concrete action (who does what).
        - health: "healthy" (no failures or errors), "degraded" (some failures, core flows work), "broken" (core flows
          such as consent creation, SCA or account access fail, or preflight blocked the run), "inconclusive"
          (mostly our-side errors, so the bank could not really be judged).
        - attention: up to 5 warnings from passed scenarios worth fixing, each with its scenario id; [] if none.
        - headline: one sentence with the verdict and the most important cause. Use the CHANGES SINCE PREVIOUS
          RUN section to say whether the problem is new; do not restate the changes otherwise.

        Reply with one JSON object only, no prose before or after, matching this JSON schema:
        """ + RunSummary.JsonSchema;

    /// <summary>Summarizes a run and stores the result. Never throws for model problems: they are stored as status failed.</summary>
    public async Task<RunSummaryResult> SummarizeAsync(string runId, string? triggeredBy = null, CancellationToken cancellationToken = default)
    {
        var run = await store.GetRunAsync(runId, cancellationToken) ?? throw new InvalidOperationException($"Run '{runId}' is not in the store");
        var settings = clients.Describe(AiFeatures.Summary);
        var sw = Stopwatch.StartNew();

        var entity = new RunSummaryEntity
        {
            Id = Guid.NewGuid(),
            RunId = runId,
            CreatedUtc = DateTime.UtcNow,
            Provider = settings.Provider!,
            Model = settings.Model!,
            Status = "failed",
            TriggeredBy = triggeredBy,
        };

        RunSummary? summary = null;
        if (run.Scenarios.Count == 0)
        {
            // Nothing for a model to explain; say so without spending a call.
            summary = new RunSummary
            {
                Headline = $"No results: the run ended as {run.Status}{(run.Error is null ? "" : $" ({run.Error})")} before any scenario finished.",
                Health = "inconclusive",
                Comparison = "Not compared: this run has no results.",
            };
            entity.Provider = "none";
            entity.Model = "none";
            entity.Status = "ok";
            entity.SummaryJson = summary.ToJson();
        }
        else
        {
            var previous = await store.GetPreviousRunAsync(run, cancellationToken);
            var digest = RunDigest.Build(run, previous);
            summary = await AskModelAsync(digest, run.Id, triggeredBy, entity, cancellationToken);
        }

        entity.DurationMs = sw.ElapsedMilliseconds;
        await store.AddRunSummaryAsync(entity, cancellationToken);
        return new RunSummaryResult(entity, summary);
    }

    private async Task<RunSummary?> AskModelAsync(RunDigest digest, string runId, string? triggeredBy, RunSummaryEntity entity, CancellationToken cancellationToken)
    {
        List<ChatMessage> messages =
        [
            new(ChatRole.System, SystemPrompt),
            new(ChatRole.User, digest.Text),
        ];

        // Two attempts: the second one shows the model why its first answer could not be read.
        for (var attempt = 1; attempt <= 2; attempt++)
        {
            var callId = Guid.NewGuid();
            var client = clients.Create(AiFeatures.Summary, new AiCallContext(runId, triggeredBy, callId));
            var options = clients.ChatOptionsFor(AiFeatures.Summary);
            if (entity.Provider == AiProviders.Ollama)
                options.ResponseFormat = ChatResponseFormat.ForJsonSchema(JsonDocument.Parse(RunSummary.JsonSchema).RootElement, "run_summary");

            ChatResponse response;
            try
            {
                response = await client.GetResponseAsync(messages, options, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
            {
                entity.AiCallId = callId;
                entity.Text = ex is OperationCanceledException
                    ? $"The model did not answer in time ({ex.GetBaseException().Message}). A local model may still be loading; try again or raise the timeout."
                    : $"{ex.GetType().Name}: {ex.GetBaseException().Message}";
                return null;
            }

            entity.AiCallId = callId;
            entity.Model = response.ModelId ?? entity.Model;

            if (response.FinishReason == ChatFinishReason.ContentFilter)
            {
                entity.Text = "The model declined to answer (refusal). Try again, or switch the summary feature to the local model.";
                return null;
            }

            try
            {
                var summary = RunSummary.Parse(response.Text);
                entity.ValidationNotes = summary.Validate(digest);
                entity.SummaryJson = summary.ToJson();
                entity.Status = "ok";
                entity.Text = null;
                return summary;
            }
            catch (JsonException ex)
            {
                entity.Status = "unstructured";
                entity.Text = response.Text;
                messages.AddRange(response.Messages);
                messages.Add(new ChatMessage(ChatRole.User, $"Your answer could not be read as the required JSON ({ex.Message}). Reply again with only the JSON object."));
            }
        }

        return null;
    }
}
