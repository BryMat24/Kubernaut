# Review: `DiagnosisAgent` token cost (~170k tokens/run, reasoning turns growing 5k → 23k+)

Source traces: `langsmith/run-019f84fb-2d46-7102-b221-f21d22f29172.json` (`reasoning_node`,
`langgraph_step: 17`) and `langsmith/run-019f84fb-3b0d-7780-a11e-87de9858ad10.json`
(`finalize_node`, `langgraph_step: 18`), same thread (`628651d4-5d23-4f8c-b8db-38bb288c5827`),
model `qwen/qwen3-coder-next` via OpenRouter.

## TL;DR

Unlike the `RemediationAgent` loop bug (`PLAN.md`), this is **not** a stuck/redundant loop — the
agent progresses through distinct, legitimate tool calls each turn. The cost comes from two
compounding, purely architectural gaps: (1) `DiagnosisAgent._reasoning_node` resends the *entire*
accumulated message history every turn with no compaction — the exact anti-pattern
`RemediationAgent` had before `HistoryCompactor` was built, except `HistoryCompactor` was
deliberately scoped only to `RemediationAgent` and never wired into `DiagnosisAgent`; and (2) one
tool, `list_resources`, returns full unfiltered `kubectl get -o json` output with no truncation,
and a single call to it added ~11k tokens by itself. `finalize_node` then re-sends that same
un-shrunk history a second time for the structured classification, roughly doubling the cost of
the run's final phase.

## Evidence

Each `AIMessage` in the trace carries its own `usage_metadata` (frozen at generation time), giving
an exact per-turn cost curve across the run's 8 reasoning turns + finalize:

| turn | tool call that produced the *next* turn's growth | input tokens (cumulative) |
|---|---|---|
| 1 | (baseline — empty history, first call) | **4,957** |
| 2 | `list_namespaces` (672 chars) | 5,248 |
| 3 | `list_resources` (**31,709 chars**) | **16,310** (+11,062) |
| 4 | `list_resources` (7,180 chars) | 18,321 (+2,011) |
| 5 | `get_events` (8,455 chars) | 21,620 (+3,299) |
| 6 | `oom_killed_pods` (1,220 chars) | 22,090 (+470) |
| 7 | `get_previous_logs` (337 chars) | 22,243 (+153) |
| 8 | `get_previous_logs` (337 chars) | 22,395 (+152) |
| final reasoning call | `get_resource` (2,651 chars) | **23,157** |
| `finalize_node` (`Classifier.classify`) | — resends the same 17-message, 54,292-char history + a finalize prompt | ~same order of magnitude as the last reasoning call (not separately captured with `usage_metadata` in this export, since it's a node-level trace, not an LLM-call trace) |

Summing (input+output) tokens across just the 9 reasoning-node LLM calls in this one run:
4,998 + 5,280 + 16,342 + 18,343 + 21,646 + 22,139 + 22,291 + 22,438 + 23,501 = **~157k tokens**,
before `finalize_node`'s own classification call is even added — consistent with the reported
**~170k tokens/run**.

**Turn 1's 4,957-token floor, before any tool has even run, is system prompt + tool schemas
alone**: `DiagnosisAgent.SYSTEM_PROMPT` is ~4,800 characters (~1.2k tokens) of source, and
`llm.bind_tools(tools)` binds all 12 k8s tools (`mcp_servers/k8s_mcp_server/k8s_tools.py`) + 5
PromQL tools (`mcp_servers/prometheus_mcp_server/promql_tools.py`) — 17 tool schemas resent in
full on every single call regardless of where the investigation is.

**`list_resources`'s single 31,709-character response** — `mcp_servers/k8s_mcp_server/k8s_tools.py:132-164`
— is `kubectl get <kind> -n <namespace> -o json`'s `items` array, returned completely raw:

```python
result = subprocess.run(cmd, capture_output=True, text=True, check=True)
data = json.loads(result.stdout)
return data.get("items", [])
```

No field pruning, no size cap — unlike `tools/file_tools.py`'s `read_file_content`, which
explicitly truncates at `MAX_CHARS = 10_000`. Raw `kubectl -o json` output is dominated by fields
with near-zero diagnostic value for an LLM: `metadata.managedFields` (a full per-field-manager
ownership ledger, often the single largest part of the object), `resourceVersion`, `uid`,
`generation`, and complete container specs duplicated per replica.

## Root causes

1. **No history compaction in `DiagnosisAgent`.** `_reasoning_node`
   (`agents/diagnosis_agent.py:113-129`) builds `messages = [SystemMessage(system_prompt)] +
   state["messages"]` every turn — the full, ever-growing history, unchanged since before
   `HistoryCompactor` existed. `HistoryCompactor` (`agents/helpers/compaction.py`) was built
   generic over `list[BaseMessage]` specifically so other agents could adopt it (documented in
   `PLAN.md`'s Architecture section), but wiring it into `DiagnosisAgent`/`PlannerAgent` was
   explicitly out of scope for that plan and was never done. This is the direct cause of the
   monotonic per-turn growth in the table above.

2. **`finalize_node` pays the same uncompacted-history cost a second time.**
   `agents/diagnosis_agent.py:170` calls `self.classifier.classify(state["query"],
   state["messages"])`, and `Classifier.classify` (`agents/helpers/classifier.py:9-15`) does
   `prompt_messages = messages + [SystemMessage(content=finalize_prompt)]` — the same full history
   the last reasoning turn already paid for, resent once more for a call whose only job is
   structuring the final verdict. If root cause 1 is fixed, this cost drops automatically, since
   `finalize_node` reads from the same (now-compacted) `state["messages"]`.

3. **`list_resources` returns raw, unfiltered `kubectl -o json` with no size cap.**
   (`mcp_servers/k8s_mcp_server/k8s_tools.py:132-164`). This is what turned one single tool call
   into an 11k-token jump — the largest single contributor in the trace. Other tools in the same
   file (`get_events`, `get_previous_logs`, `oom_killed_pods`, `get_resource`) stayed in the
   hundreds-to-low-thousands of characters in this run, so they aren't ruled out as similarly
   unbounded, but this trace's evidence points squarely at `list_resources`.

4. **Fixed ~3-4k token tool-schema floor on every call**, from binding all 17 k8s+PromQL tools
   regardless of investigation stage. This compounds root cause 1's growth rather than being a
   one-time setup cost — it's paid again on every one of the 9 calls in the table above. Whether
   trimming the bound tool set is worthwhile isn't established by this trace alone (all tools
   called here were legitimately relevant to this query), so this is noted as lower-confidence
   than 1-3.

## Recommended fixes

**Fix root cause 1 (highest leverage — same mechanism, already built):** wire the existing
`HistoryCompactor` into `DiagnosisAgent`, the same way it's wired into `RemediationAgent`
(`agents/remediation_agent.py:144` constructs it from the raw, un-tool-bound `llm`;
`agents/remediation_agent.py:167-168` calls `self.history_compactor.compact(state["messages"])`
before building the LLM call). `HistoryCompactor` has zero `RemediationAgent`-specific coupling —
this is exactly the reuse case it was designed for. This alone should flatten the 4,957 → 23,157
growth curve into a bounded cost per turn, and (via root cause 2) shrinks `finalize_node`'s cost
for free.

**Fix root cause 3:** cap/filter `list_resources`'s output — at minimum strip
`metadata.managedFields` from each item before returning (it carries no diagnostic value and is
frequently the largest field on the object), and consider a `MAX_CHARS`-style truncation-with-marker
matching `read_file_content`'s existing pattern for the rare case a namespace has enough resources
to still be huge after that.

**Investigate root cause 4 as a follow-up, not a first fix:** measure whether a smaller,
stage-appropriate tool subset (e.g. not binding PromQL tools when the query is purely
resource/event-shaped) meaningfully reduces the per-call floor, before spending effort on it —
this trace doesn't have enough evidence to size the win.

## Suggested priority

1. Wire `HistoryCompactor` into `DiagnosisAgent` (fixes 1 and 2 together, reuses existing code).
2. Trim/cap `list_resources`'s output (fixes the single largest one-shot jump).
3. Investigate whether other k8s tools (`describe_resource`, `get_events` at scale) need the same
   cap — not evidenced as a problem in this trace, but same class of risk as `list_resources`.
4. Investigate tool-schema-floor reduction (root cause 4) only after 1-2 land, since it's the
   smallest and least-evidenced contributor here.
