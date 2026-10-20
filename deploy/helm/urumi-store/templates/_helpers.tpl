{{- define "urumi-store.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: urumi-store
{{- with .Values.store.slug }}
urumi.io/store-slug: {{ . }}
{{- end }}
{{- end }}

{{/* The WordPress image (also used by the setup Job, which needs WP-CLI and WooCommerce). */}}
{{- define "urumi-store.wordpressImage" -}}
{{- $img := .Values.wordpress.image }}
{{- if $img.digest }}
{{- printf "%s/%s@%s" $img.registry $img.repository $img.digest }}
{{- else }}
{{- printf "%s/%s:%s" $img.registry $img.repository $img.tag }}
{{- end }}
{{- end }}

{{- define "urumi-store.dnsEgress" -}}
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
      podSelector:
        matchLabels:
          k8s-app: kube-dns
  ports:
    - { port: 53, protocol: UDP }
    - { port: 53, protocol: TCP }
{{- end }}
