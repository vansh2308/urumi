#!/usr/bin/env bash
# Back up the store before an upgrade: a consistent MariaDB dump and the WordPress files
# (wp-content: uploads, plugins, themes). Read-only for the cluster; writes two files locally.
#
#   deploy/scripts/backup-store.sh [namespace] [release]     # defaults: urumi store
#   BACKUP_DIR=/secure/place deploy/scripts/backup-store.sh
#
# The root password is read from the chart's Secret inside the pod's environment, never printed
# or passed on the command line. Restore steps are in docs/upgrades.md.
set -euo pipefail

NAMESPACE="${1:-urumi}"
RELEASE="${2:-store}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-${ROOT}/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"
umask 077

db="${BACKUP_DIR}/${RELEASE}-db-${STAMP}.sql.gz"
files="${BACKUP_DIR}/${RELEASE}-wp-content-${STAMP}.tar.gz"

# --single-transaction: a consistent snapshot of InnoDB tables without locking the store.
# The password goes through an option file on a pipe, so it is not in any process's arguments.
kubectl -n "${NAMESPACE}" exec "statefulset/${RELEASE}-mariadb" -c mariadb -- bash -c '
  pw="$(cat "${MARIADB_ROOT_PASSWORD_FILE}" 2>/dev/null || printf %s "${MARIADB_ROOT_PASSWORD:-}")"
  exec mariadb-dump --defaults-extra-file=<(printf "[client]\nuser=root\npassword=%s\n" "$pw") \
    --single-transaction --routines --triggers --all-databases' \
  | gzip > "${db}"
kubectl -n "${NAMESPACE}" exec "deploy/${RELEASE}-wordpress" -c wordpress -- \
  tar -C /bitnami/wordpress -czf - wp-content > "${files}"

gzip -t "${db}"
tar -tzf "${files}" >/dev/null
ls -lh "${db}" "${files}"
