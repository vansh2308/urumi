{{- define "urumi-service.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "urumi-service.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "urumi-service.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "urumi-service.selectorLabels" -}}
app.kubernetes.io/name: {{ include "urumi-service.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "urumi-service.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "urumi-service.selectorLabels" . }}
app.kubernetes.io/version: {{ include "urumi-service.tag" . | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.component }}
app.kubernetes.io/component: {{ . }}
{{- end }}
{{- with .Values.partOf }}
app.kubernetes.io/part-of: {{ . }}
{{- end }}
{{- end }}

{{/* image.tag, else the umbrella's global.imageTag, else the chart appVersion. */}}
{{- define "urumi-service.tag" -}}
{{- $global := (.Values.global | default dict).imageTag | default "" }}
{{- .Values.image.tag | default $global | default .Chart.AppVersion }}
{{- end }}

{{- define "urumi-service.image" -}}
{{- $repo := required "image.repository is required" .Values.image.repository }}
{{- if .Values.image.digest }}
{{- printf "%s@%s" $repo .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" $repo (include "urumi-service.tag" .) }}
{{- end }}
{{- end }}

{{- define "urumi-service.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "urumi-service.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Secret references as container env entries. */}}
{{- define "urumi-service.secretEnv" -}}
{{- range .Values.secretEnv }}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ required "secretEnv[].secretName is required" .secretName }}
      key: {{ default .name .key }}
      {{- if hasKey . "optional" }}
      optional: {{ .optional }}
      {{- end }}
{{- end }}
{{- end }}

{{/* hostAliases: literal entries plus host names pinned to existing Services' ClusterIPs. */}}
{{- define "urumi-service.hostAliases" -}}
{{- $entries := list }}
{{- range .Values.hostAliases }}
{{- $entries = append $entries . }}
{{- end }}
{{- range .Values.hostAliasesFromServices }}
{{- $ip := .ip }}
{{- if not $ip }}
{{- $svc := lookup "v1" "Service" (default $.Release.Namespace .namespace) .service }}
{{- if $svc }}
{{- $ip = $svc.spec.clusterIP }}
{{- end }}
{{- end }}
{{- if $ip }}
{{- $entries = append $entries (dict "ip" $ip "hostnames" .hostnames) }}
{{- end }}
{{- end }}
{{- if $entries }}
hostAliases:
{{- toYaml $entries | nindent 2 }}
{{- end }}
{{- end }}

{{- define "urumi-service.probe" -}}
httpGet:
  path: {{ .probe.path }}
  port: http
periodSeconds: {{ .probe.periodSeconds | default 10 }}
timeoutSeconds: {{ .probe.timeoutSeconds | default 3 }}
failureThreshold: {{ .probe.failureThreshold | default 3 }}
{{- with .probe.initialDelaySeconds }}
initialDelaySeconds: {{ . }}
{{- end }}
{{- end }}
