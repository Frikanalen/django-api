{{/*
Common environment variables for Django containers
*/}}
{{- define "django.env" }}
- name: DJANGO_SETTINGS_MODULE
  value: {{ .Values.env.DJANGO_SETTINGS_MODULE }}
- name: ALLOWED_HOSTS
  value: {{ .Values.env.ALLOWED_HOSTS }}
- name: SMTP_SERVER
  value: {{ .Values.env.SMTP_SERVER }}
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
{{- end }}
