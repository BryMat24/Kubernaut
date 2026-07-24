---
playbook_id: scheduling
category: scheduling
trigger_conditions:
  - "one or more pods are stuck Pending"
  - "a FailedScheduling event is present"
---

# Scheduling / Pending Pod Failure

## Checklist
1. tool: describe_resource   params: {kind: POD, name, namespace}   conclusive: false
2. tool: get_events          params: {namespace}                    conclusive: true
   conclusion_criteria: >
     read the FailedScheduling reason: 'insufficient cpu/memory' → capacity (confirm with
     top_nodes); 'untolerated taint' → a node taint with no matching pod toleration; taints named
     node.kubernetes.io/disk-pressure or memory-pressure → node health pressure.
3. tool: get_node_conditions   params: {name}   conclusive: true
   conclusion_criteria: >
     DiskPressure/MemoryPressure=True explains auto-applied NoSchedule taints; or the node's
     taints field shows the specific untolerated taint.
4. tool: top_nodes   conclusive: true
   note: only for the insufficient-resources branch — show allocatable < pod request.

## Conclusion
Name the specific scheduling barrier (insufficient CPU/memory vs. untolerated taint vs. node
pressure) and the node/pod involved.

## Do not conclude
- Image pull failure or CrashLoopBackOff.
- The pod's own resource requests are "misconfigured" when the real cause is a taint/capacity.
- An arbitrary/manual taint unrelated to node health when the taint is pressure-induced.
