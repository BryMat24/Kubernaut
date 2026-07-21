from models import DiagnosisResult, RemediationPlan


def build_summary_message(
    diagnosis: DiagnosisResult,
    plan: RemediationPlan | None = None,
    approved: bool | None = None,
    pr_url: str | None = None,
) -> str:
    lines = [diagnosis.summary]

    if diagnosis.root_cause:
        lines.append(f"\nRoot cause: {diagnosis.root_cause}")

    if plan is not None:
        lines.append(f"\nProposed fix: {plan.summary}")
        if approved is None:
            lines.append("\n(Awaiting your approval)")
        elif approved:
            if pr_url:
                lines.append(f"\nApproved — PR opened: {pr_url}")
            else:
                lines.append("\nApproved, but no PR was opened.")
        else:
            lines.append("\nNot approved — no changes were made.")

    return "\n".join(lines)
