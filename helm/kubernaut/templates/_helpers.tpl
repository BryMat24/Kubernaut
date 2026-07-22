{{- define "kubernaut.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "kubernaut.labels" -}}
app.kubernetes.io/name: {{ include "kubernaut.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "kubernaut.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kubernaut.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
