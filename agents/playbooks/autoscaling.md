---
playbook_id: autoscaling
category: autoscaling
trigger_conditions:
  - "an HPA is not scaling, or is stuck at min/max replicas"
  - "reports of load not being handled, or HPA metric unknown/missing"
---

# HorizontalPodAutoscaler Failure

## Checklist
1. tool: get_resource   params: {kind: HORIZONTALPODAUTOSCALER, name, namespace}   conclusive: true
   conclusion_criteria: >
     If a condition shows ScalingLimited=True reason TooManyReplicas AND currentReplicas ==
     maxReplicas → the HPA works but maxReplicas is too low. If the current CPU metric is
     unknown/missing or a condition says it could not compute the resource metric → check step 2.
2. tool: get_resource   params: {kind: DEPLOYMENT, name, namespace}   conclusive: true
   conclusion_criteria: >
     container has no resources.requests.cpu set → HPA cannot compute CPU utilization. This is
     the cause, NOT a metrics-server outage.

## Conclusion
Either maxReplicas is below observed demand (HPA healthy), or the target Deployment is missing a
CPU request so utilization can't be computed. State which, and name the HPA and Deployment.

## Do not conclude
- The Deployment is unhealthy or crashlooping.
- The cluster's metrics-server is down or uninstalled (unless top_pods/top_nodes also fail).
- Insufficient node resources.
- Autoscaling is "broken" when it is capped and working correctly.
