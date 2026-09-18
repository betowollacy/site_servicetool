#!/usr/bin/env bash
# Restaura o backup do servico a partir dos arquivos salvos em /opt/site_servicetool/backups
#
# Uso:
#   ./restore_backup.sh [DATA-HORA]   ex.: ./restore_backup.sh 20260918-040315
#   (sem argumento usa o backup mais recente)
#
# Atencao: sobrescreve o banco de dados atual! Pare/avise antes de executar em producao.

set -e

APP=/opt/site_servicetool
BK=$APP/backups
TS="$1"

if [ ! -d "$BK" ]; then
    echo "ERRO: pasta de backups nao existe ($BK)."
    exit 1
fi

if [ -z "$TS" ]; then
    TS=$(ls -1t "$BK"/db-*.sqlite3 2>/dev/null | head -1 | xargs -r basename | sed 's/^db-//; s/\.sqlite3$//')
fi

DB="$BK/db-$TS.sqlite3"
MEDIA="$BK/media-$TS.tar.gz"

if [ ! -f "$DB" ]; then
    echo "ERRO: backup do banco nao encontrado -> $DB"
    exit 1
fi
if [ ! -f "$MEDIA" ]; then
    echo "ERRO: backup de midias nao encontrado -> $MEDIA"
    exit 1
fi

echo "==> Parando o servico..."
systemctl stop servicetool.service 2>/dev/null || true

echo "==> Restaurando banco de dados ($DB)"
cp -f "$DB" "$APP/db.sqlite3"
chmod 644 "$APP/db.sqlite3"

echo "==> Restaurando midias ($MEDIA)"
mkdir -p "$APP/media"
tar xzf "$MEDIA" -C "$APP"

echo "==> Subindo o servico..."
systemctl start servicetool.service

echo "Restauracao concluida com sucesso."