---
playbook_id: network
category: network
trigger_conditions:
    - "connection refused"
    - "connection timeout"
    - "service unreachable"
    - "5xx errors with little or no application error logs"
    - "requests fail between services"
---

# Network / Connectivity Failure

## Checklist

### 1. Verify the Service exists

tool: list_resources
params: {kind: service, namespace}
conclusive: false

note: The Service name may differ from the Deployment name.

---

### 2. Inspect the Service configuration

tool: get_resource
params: {kind: service, name, namespace}
conclusive: false

Check:

- selector matches workload labels
- Service type is expected
- ports and targetPorts are correct

Possible findings:

- selector matches zero Pods
- wrong targetPort
- wrong protocol
- incorrect Service type

---

### 3. Check Service endpoints

tool: check_service_connectivity
params: {service, namespace}
conclusive: false

Check:

- endpoint count
- endpoints ready
- endpoints empty

Possible findings:

- selector mismatch
- Pods not Ready
- rollout left no healthy Pods

---

### 4. Verify workload health

tool: list_resources
params: {kind: pod, namespace, label_selector}
conclusive: false

Check:

- Ready
- Running
- CrashLoopBackOff
- Restart count

Reason:

A networking problem should not actually be caused by unhealthy Pods.

---

### 5. Check Ingress (only if traffic enters through Ingress)

tool: get_resource
params: {kind: ingress, name, namespace}
conclusive: false

Check:

- backend Service
- backend port
- host
- path

Possible findings:

- incorrect backend Service
- incorrect backend port
- missing path
- missing host

---

### 6. Check NetworkPolicy

tool: list_resources
params: {kind: networkpolicy, namespace}
conclusive: false

If policies exist:

tool: get_resource
params: {kind: networkpolicy, name, namespace}

Possible findings:

- ingress denied
- egress denied
- namespace selector mismatch
- pod selector mismatch

---

### 7. Check application error rate

tool: error_rate
params: {app_label}
conclusive: false

Purpose:

Determine whether failures affect all replicas rather than one Pod.

---

### 8. Check application logs

tool: error_logs
params: {app_label, namespace}
conclusive: false

Purpose:

Determine whether failures originate inside the application instead of networking.

---

### 9. Check Kubernetes events

tool: get_events
params: {namespace}
conclusive: true

Look for:

- Service updated
- Ingress updated
- NetworkPolicy created
- rollout immediately preceding failures

---

### 10. Check DNS resolution

tool: list_resources
params: {kind: service, namespace}
conclusive: false

Check:

- does a Service with the exact hostname/name the client is trying to reach actually exist in
  this namespace (or the referenced namespace, if the client uses a fully-qualified name)?

tool: get_events
params: {namespace}
conclusive: false

Check:

- CoreDNS pods in kube-system Running and Ready (list_resources params: {kind: pod, namespace:
  kube-system, label_selector: k8s-app=kube-dns})

Possible findings:

- the target Service name does not exist at all -- DNS has nothing to resolve
- the client is using the wrong namespace suffix (cross-namespace DNS needs
  <service>.<namespace>.svc.cluster.local)
- CoreDNS itself is unhealthy (Pods not Ready) -- rare, check this last

---

### 11. Check LoadBalancer status (only if Service type is LoadBalancer)

tool: get_resource
params: {kind: service, name, namespace}
conclusive: true

Check:

- status.loadBalancer.ingress is empty/absent
- no cloud-controller-manager or LoadBalancer implementation exists in this cluster

Possible findings:

- the Service is otherwise correctly configured (selector matches, endpoints ready) but stuck
  Pending because nothing in this cluster can provision an external IP

---

## Conclusion

A network/connectivity root cause is supported when the evidence identifies one of the following:

- Service selector matches no Pods
- Service endpoints are empty because Pods are not selected
- targetPort does not match the container port
- Ingress routes to the wrong Service or wrong port
- NetworkPolicy blocks ingress or egress traffic
- Traffic fails uniformly across replicas while Pods remain healthy
- A recent Service, Ingress, or NetworkPolicy change correlates with the incident

State exactly which networking component is responsible.

Examples:

- Service selector mismatch
- incorrect targetPort
- Ingress backend misconfiguration
- NetworkPolicy blocking traffic
- no Ready endpoints

## Do not conclude

- CrashLoopBackOff
- ImagePullBackOff
- Failed scheduling
- OOMKilled
- CPU or memory exhaustion
- Deployment rollout failure
- Application exceptions found in logs
