{{- define "django.env" -}}
{{- range $key, $value := .Values.env }}
- name: {{ $key }}
  value: {{ $value | quote }}
{{- end }}
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.django.secretName }}
      key: SECRET_KEY
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.secret }}
      key: DATABASE_URL
{{- with .Values.pullUrlKeys.secret }}
- name: FK_PULL_URL_KEYS
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: FK_PULL_URL_KEYS
{{- end }}
{{- end -}}
