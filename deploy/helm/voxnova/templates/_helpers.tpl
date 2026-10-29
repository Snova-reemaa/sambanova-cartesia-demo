{{- define "voxnova.name" -}}{{ .Release.Name }}{{- end -}}

{{- define "voxnova.image" -}}
{{- $tag := required "set image.tag to the short SHA the image was pushed under" (default .Chart.AppVersion .Values.image.tag) -}}
{{ .Values.image.registry }}/{{ .Values.image.name }}:{{ $tag }}
{{- end -}}

{{- define "voxnova.labels" -}}
app.kubernetes.io/name: voxnova
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- /* The sign-in proxy's own name label, so nothing selecting the app (its Service, its
NetworkPolicy) ever selects the proxy too. */ -}}
{{- define "voxnova.authLabels" -}}
app.kubernetes.io/name: voxnova-oauth2-proxy
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
