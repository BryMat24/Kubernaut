---
playbook_id: network
category: network
trigger_conditions:
  - "5xx or connection-refused/timeout errors present"
  - "no matching application-level error logs in the same window"
---

# Network / Connectivity Failure

## Checklist
1. tool: check_service_connectivity   params: {service, namespace}   conclusive: false
   note: confirm the Service name first with list_resources(kind=service) — a Service name is
   not necessarily its Deployment name.
2. tool: error_rate                    params: {app, window}          conclusive: false
   note: near-uniform errors across all pods point to network/service, not a single bad pod.
3. tool: get_events                    params: {namespace}            conclusive: true
   conclusion_criteria: >
     endpoints empty or all-not-ready AND no matching app-level error logs in the window
     AND a Service/NetworkPolicy change is visible in events within the window.

## Conclusion
root_cause = network iff endpoints are unhealthy/empty while errors are near-uniform across
pods (not isolated to one) and a correlated recent change is present.

## Do not conclude
- A single crashing pod — that is an application/workload failure (use the rollout or generic
  playbook), not a network fault.
