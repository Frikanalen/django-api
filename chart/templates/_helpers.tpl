{{- define "django.env" -}}
- name: CSRF_TRUSTED_ORIGINS
  value: {{ .Values.env.CSRF_TRUSTED_ORIGINS }}
- name: DJANGO_SETTINGS_MODULE
  value: {{ .Values.env.DJANGO_SETTINGS_MODULE }}
- name: ALLOWED_HOSTS
  value: {{ .Values.env.ALLOWED_HOSTS }}
# Set explicitly per environment: the settings default to the production URL,
# so an environment that omits this would advertise production's upload
# endpoint. Must match the path the ingest chart serves tusd on.
- name: FK_UPLOAD_URL
  value: {{ .Values.env.FK_UPLOAD_URL | quote }}
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
