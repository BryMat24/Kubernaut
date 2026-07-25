from langchain_core.messages import BaseMessage, SystemMessage

from models import EvaluationVerdict


class DiagnosisEvaluator:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(EvaluationVerdict, include_raw=True)

    async def evaluate(
        self,
        query: str,
        hypothesis: str,
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
            You are evaluating whether the investigation above has reached a confident,
            evidence-backed root cause. Judge the finding on its own merits -- a well-evidenced
            conclusion is valid even if it doesn't match the hypothesis being investigated (the
            investigator may have correctly ruled that hypothesis out and pointed at the real
            mechanism instead).

            user query:
            {query}

            hypothesis being investigated:
            {hypothesis}

            budget:
            {budget_note}

            What separates a confident finding from an unconfident one:

            BAD (not confident) example:
            "The pod might be failing due to a resource issue or possibly a misconfiguration.
            I couldn't fully confirm the cause, but it could be related to permissions or the
            image."
            -- hedges with "might"/"possibly"/"could be", names no specific value, field, or
            message actually observed via a tool call.

            GOOD (confident) example:
            "The container's command is set to /someunknowncommand, which does not exist in the
            busybox:1.36 image. describe_resource shows containerStatuses[0].state.waiting.reason
            is CreateContainerError. This is a bad entrypoint, not a secret/configmap or
            permissions issue."
            -- names the specific mechanism, cites the exact evidence observed, states it plainly.

            Decide one verdict:
            - conclusive: the final answer reads like the GOOD example -- a specific mechanism
              backed by specific evidence actually observed via a tool call. This applies whether
              or not it confirms the hypothesis above.
            - reformulate: the final answer reads like the BAD example (hedged, vague, or no
              specific evidence) and the hypothesis appears ruled out -- a different hypothesis is
              worth trying next. Set next_hypothesis and why_ruled_out. Only use this if attempts
              remain.
            - exhausted: no confident conclusion and no new hypothesis worth pursuing (or the
              budget is spent).

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
