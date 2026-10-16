{{- define "to-do-board.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "to-do-board.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "to-do-board.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "to-do-board.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "to-do-board.selectorLabels" -}}
app.kubernetes.io/name: {{ include "to-do-board.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "to-do-board.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "to-do-board.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "to-do-board.image" -}}
{{- if .Values.image.digest }}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) }}
{{- end }}
{{- end }}

{{/* Адрес доски для ссылок в Telegram: явный config.publicUrl или https://<ingress.host>. */}}
{{- define "to-do-board.publicUrl" -}}
{{- if .Values.config.publicUrl }}
{{- .Values.config.publicUrl }}
{{- else if and .Values.ingress.enabled .Values.ingress.host }}
{{- printf "%s://%s" (ternary "https" "http" .Values.ingress.tls.enabled) .Values.ingress.host }}
{{- end }}
{{- end }}

{{/* Аннотации для bank-vaults vault-secrets-webhook и vault-secrets-reloader. */}}
{{- define "to-do-board.vaultAnnotations" -}}
{{- with .Values.vault }}
{{- if .enabled }}
{{- with .addr }}
vault.security.banzaicloud.io/vault-addr: {{ . | quote }}
{{- end }}
{{- with .role }}
vault.security.banzaicloud.io/vault-role: {{ . | quote }}
{{- end }}
{{- with .authPath }}
vault.security.banzaicloud.io/vault-path: {{ . | quote }}
{{- end }}
{{- with .skipVerify }}
vault.security.banzaicloud.io/vault-skip-verify: {{ . | quote }}
{{- end }}
{{- if .reloadOnSecretChange }}
secrets-reloader.security.bank-vaults.io/reload-on-secret-change: "true"
{{- end }}
{{- with .annotations }}
{{ toYaml . }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
