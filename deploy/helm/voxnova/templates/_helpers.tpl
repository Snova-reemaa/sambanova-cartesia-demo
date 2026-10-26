{{- define "voxnova.name" -}}{{ .Release.Name }}{{- end -}}

{{- define "voxnova.image" -}}
{{- $tag := required "set image.tag to the short SHA the image was pushed under" (default .Chart.AppVersion .Values.image.tag) -}}
{{ .Values.image.registry }}/{{ .Values.image.name }}:{{ $tag }}
{{- end -}}

{{- define "voxnova.labels" -}}
app.kubernetes.io/name: voxnova
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
