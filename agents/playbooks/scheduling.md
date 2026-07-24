---
playbook_id: scheduling
category: scheduling
trigger_conditions:
    - "one or more pods are stuck Pending"
    - "a FailedScheduling event is present"
    - "a node reports NotReady or a pressure condition"
---

# Scheduling / Pending Pod Failure

## Checklist

1. tool: describe_resource params: {kind: POD, name, namespace} conclusive: false
2. tool: get_events params: {namespace} conclusive: true
   conclusion_criteria: >
   read the FailedScheduling reason: 'insufficient cpu/memory' → capacity (confirm with
   top_nodes); 'untolerated taint' → a node taint with no matching pod toleration; taints named
   node.kubernetes.io/disk-pressure, memory-pressure, or pid-pressure → node health pressure.
3. tool: get_node_conditions params: {name} conclusive: true
   conclusion_criteria: >
   DiskPressure/MemoryPressure/PIDPressure=True explains the matching auto-applied NoSchedule
   taint (node.kubernetes.io/disk-pressure, memory-pressure, pid-pressure respectively); or the
   node's taints field shows the specific untolerated taint. PIDPressure specifically means the
   node is close to exhausting available process IDs (too many processes/threads on the node),
   not disk or memory.
4. tool: top_nodes conclusive: true
   note: only for the insufficient-resources branch — show allocatable < pod request.

## Conclusion

Name the specific scheduling barrier (insufficient CPU/memory vs. untolerated taint vs. node
pressure — disk, memory, or PID) and the node/pod involved.

## Limitations

If `get_node_conditions` shows Ready=False (Node NotReady), or you suspect the kubelet itself has
stopped reporting: state that the node is NotReady and name it, but do not speculate about _why_
the kubelet stopped — this toolset has no way to inspect kubelet process state, node system logs,
or restart anything. Recommend a human check the node directly.

## Do not conclude

- Image pull failure or CrashLoopBackOff.
- The pod's own resource requests are "misconfigured" when the real cause is a taint/capacity.
- An arbitrary/manual taint unrelated to node health when the taint is pressure-induced.
