{{- define "django.env" -}}
- name: CSRF_TRUSTED_ORIGINS
  value: {{ .Values.env.CSRF_TRUSTED_ORIGINS }}
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
{{- end -}}
