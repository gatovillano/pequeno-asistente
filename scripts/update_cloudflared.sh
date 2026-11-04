#!/usr/bin/env bash
cp "/home/gato/Proyectos/Pequeño asistente/config_cloudflared_api-pa.yml" /etc/cloudflared/config.yml
systemctl restart cloudflared
