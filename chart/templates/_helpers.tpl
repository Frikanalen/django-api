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
{{- end -}}
