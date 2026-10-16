#!/usr/bin/env bash
# Export this box's gamma configuration, for gamma_config_import.sh on another box (dev -> p, decided
# 2026-10-02): the config tables (badges, badge<->rule links, rules, statuses, and the event types and
# configurations the rules point at) as a data-only SQL dump with column names, the same tables as TSV (for
# the import's dry-run plan), their column lists, and every image file the badges and statuses reference
# (image, thumbnail) as a tarball of gamma's media folder. SELECT only; writes nothing but OUTDIR.
# Usage: bash gamma_config_export.sh OUTDIR
set -Eeuo pipefail
OUT=${1:?usage: $0 OUTDIR}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
export MYSQL_PWD=$($HOME/tutor-venv/bin/tutor config printvalue MYSQL_ROOT_PASSWORD)
MEDIA=$HOME/.local/share/tutor/data/rgg
TABLES="badges_badge badges_badge_rules rules_rule statuses_status events_eventtype events_eventconfiguration"
q(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot --batch --default-character-set=utf8mb4 rgg "$@"; }

docker exec -e MYSQL_PWD tutor_local-mysql-1 mysqldump -uroot --single-transaction --set-gtid-purged=OFF --hex-blob \
  --no-tablespaces --default-character-set=utf8mb4 --no-create-info --complete-insert --skip-triggers rgg $TABLES \
  </dev/null | zstd -q -3 > "$OUT/gamma-config.sql.zst"
for t in $TABLES; do q -e "SELECT * FROM $t ORDER BY id" </dev/null > "$OUT/$t.tsv"; done
IN_LIST=$(printf "'%s'," $TABLES); IN_LIST=${IN_LIST%,}
q -N -e "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'rgg'
         AND TABLE_NAME IN ($IN_LIST) ORDER BY 1, 2" </dev/null > "$OUT/columns.tsv"
q -N -e "SELECT image FROM badges_badge WHERE image <> '' UNION SELECT thumbnail FROM badges_badge WHERE thumbnail <> ''
         UNION SELECT image FROM statuses_status WHERE image <> ''" </dev/null | sort -u > "$OUT/media-files.txt"
missing=$(cd "$MEDIA" && while read -r f; do [ -f "$f" ] || echo "$f"; done < "$OUT/media-files.txt")
[ -z "$missing" ] || { echo "referenced but missing in $MEDIA:"; echo "$missing"; exit 1; }
tar -C "$MEDIA" -czf "$OUT/media.tgz" -T "$OUT/media-files.txt"
q -N -e "SELECT 'gamma', '$(docker exec tutor_local-rgg-1 git -C /rgg/gamma log --format=%h -1)', UTC_TIMESTAMP()" </dev/null > "$OUT/source.txt"
(cd "$OUT" && sha256sum gamma-config.sql.zst media.tgz *.tsv media-files.txt source.txt > SHA256SUMS)
echo "exported: $(for t in $TABLES; do printf '%s %d, ' "$t" $(( $(wc -l < "$OUT/$t.tsv") - 1 )); done)media files $(wc -l < "$OUT/media-files.txt") ($(du -h "$OUT/media.tgz" | cut -f1) tgz) -> $OUT"
