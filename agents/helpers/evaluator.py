from langchain_core.messages import BaseMessage, SystemMessage

from models import EvaluationVerdict


class DiagnosisEvaluator:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(EvaluationVerdict, include_raw=True)

    async def evaluate(
        self,
        query: str,
        hypothesis: str,
        playbook_body: str,
        messages: list[BaseMessage],
        hypothesis_count: int,
        max_hypotheses: int,
    ) -> EvaluationVerdict:
        budget_note = (
            "This is the final hypothesis allowed; if the evidence is not conclusive you must "
            "return 'exhausted', not 'reformulate'."
            if hypothesis_count >= max_hypotheses
            else f"{max_hypotheses - hypothesis_count} more hypothesis attempt(s) remain if needed."
        )
        prompt = f"""
            You are evaluating whether the investigation above has reached a confident root cause
            for the current hypothesis.

            user query:
            {query}

            current hypothesis:
            {hypothesis}

            playbook conclusion criteria used:
            {playbook_body}

            budget:
            {budget_note}

            Decide one verdict:
            - conclusive: the evidence supports a confident root cause. Set requires_remediation
              true if the cause is fixable via a Kubernetes config/manifest change, false if it is
              an application-level bug or an informational finding with nothing to fix.
            - reformulate: the current hypothesis is ruled out but a different, specific hypothesis
              is worth trying. Set next_hypothesis and why_ruled_out. Only use this if attempts remain.
            - exhausted: no confident conclusion and no new hypothesis worth pursuing (or the budget
              is spent).

            Respond with structured output.
        """
        response = await self.llm.ainvoke(messages + [SystemMessage(content=prompt)])
        parsed = response["parsed"]
        if parsed is None:
            return EvaluationVerdict(
                verdict="exhausted",
                reasoning=response["raw"].content or "structured output unavailable",
            )
        return parsed
