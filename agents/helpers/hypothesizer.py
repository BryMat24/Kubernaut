from langchain_core.messages import SystemMessage

from models import HypothesisSelection


class Hypothesizer:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(HypothesisSelection, include_raw=True)

    async def select(
        self,
        query: str,
        scope_summary: str,
        triggers: list[dict],
        ruled_out: list[dict],
    ) -> HypothesisSelection:
        triggers_text = "\n".join(
            f"- {t['playbook_id']} ({t['category']}): " + "; ".join(t["trigger_conditions"])
            for t in triggers
        ) or "(no playbooks available)"
        ruled_out_text = "\n".join(
            f"- {r['hypothesis']} — ruled out because: {r['why_ruled_out']}" for r in ruled_out
        ) or "(none yet)"

        prompt = f"""
            You are triaging a Kubernetes incident like an SRE. Based on the scope summary,
            pick the single most likely leading hypothesis and the playbook to investigate it
            under.

            user query:
            {query}

            scope summary (initial cluster signals gathered):
            {scope_summary}

            available playbooks (playbook_id: trigger conditions):
            {triggers_text}

            hypotheses already ruled out (do NOT re-propose these or their playbooks):
            {ruled_out_text}

            Respond with structured output: hypothesis (one sentence), playbook_id (choose the
            best-matching playbook_id from the list above, or 'generic' if none clearly fits),
            and reasoning. Never pick a playbook whose hypothesis was already ruled out.
        """

        response = await self.llm.ainvoke([SystemMessage(content=prompt)])
        parsed = response["parsed"]
        if parsed is None:
            return HypothesisSelection(
                hypothesis="Could not form a specific hypothesis; using generic investigation.",
                playbook_id="generic",
                reasoning=response["raw"].content or "structured output unavailable",
            )
        return parsed
