#!/usr/bin/env bash
# Import a gamma configuration exported by gamma_config_export.sh (dev -> p, decided 2026-10-02) into this
# box's gamma: badges, badge<->rule links, rules (event configurations re-mapped by event type NAME), statuses,
# and the badges'/statuses' image files. Only fills an EMPTY configuration; ids are kept. The box's gamma
# must already run the source's version (same columns); the plan blocks otherwise.
# DRY RUN by default: captures this box's state read-only and prints the plan (gamma_config_plan.py).
# APPLY=1 (only when the plan says READY): one transaction for the rows, then the image files, then checks
# that every count matches the export and every rule points at the same event type as on the source.
# COPY_AWARDS=1 also sets each event configuration's per-event points to the source's values.
# TARGET_DB / MEDIA_DIR override the database and media folder (for tests against a scratch copy).
# Usage: CONFIRM_HOST=<LMS_HOST> [APPLY=1] [COPY_AWARDS=1] bash ~/gamma-backfill/gamma_config_import.sh EXPORT_DIR
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
EXP=$(cd "${1:?usage: $0 EXPORT_DIR}" && pwd)
cd "$(dirname "$0")"
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
DB=${TARGET_DB:-rgg}
MEDIA=${MEDIA_DIR:-$HOME/.local/share/tutor/data/rgg}
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
q(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot --batch --default-character-set=utf8mb4 "$@"; }
(cd "$EXP" && sha256sum -c --quiet SHA256SUMS) || { echo "export files don't match their SHA256SUMS"; exit 1; }
CONFIG="badges_badge badges_badge_rules rules_rule statuses_status"
EVENTS="events_eventtype events_eventconfiguration"
IN_LIST=$(printf "'%s'," $CONFIG $EVENTS); IN_LIST=${IN_LIST%,}
OUT=$(mktemp -d); trap 'rm -rf "$OUT"' EXIT

# ---- this box's state (read-only) ----
q -N -e "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '$DB'
         AND TABLE_NAME IN ($IN_LIST) ORDER BY 1, 2" </dev/null > "$OUT/columns.tsv"
{ echo -e "table\trows"; for t in $CONFIG achievements_achievement; do printf '%s\t%s\n' "$t" "$(q -N "$DB" -e "SELECT COUNT(*) FROM $t" </dev/null 2>/dev/null || echo 0)"; done; } > "$OUT/counts.tsv"
for t in $EVENTS; do q "$DB" -e "SELECT * FROM $t ORDER BY id" </dev/null > "$OUT/$t.tsv"; done
(cd "$MEDIA" 2>/dev/null && find uploads -type f 2>/dev/null) > "$OUT/media-existing.txt" || true
plan=0; python3 gamma_config_plan.py "$EXP" "$OUT" || plan=$?
[ "${APPLY:-0}" = 1 ] || { echo "DRY RUN: nothing written"; exit 0; }
[ "$plan" = 0 ] || { echo "refusing to apply: the plan is blocked"; exit 1; }

# ---- apply ----
cols(){ q -N -e "SELECT GROUP_CONCAT(CONCAT('\`', COLUMN_NAME, '\`') ORDER BY ORDINAL_POSITION) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = '$DB' AND TABLE_NAME = '$1'" </dev/null; }
q -e "DROP DATABASE IF EXISTS rgg_cfg; CREATE DATABASE rgg_cfg" </dev/null
for t in $CONFIG $EVENTS; do q -e "CREATE TABLE rgg_cfg.$t LIKE $DB.$t" </dev/null; done
zstd -dc "$EXP/gamma-config.sql.zst" | q rgg_cfg
rule_sel=$(q -N -e "SELECT GROUP_CONCAT(IF(COLUMN_NAME = 'event_configuration_id', 'm.target_id', CONCAT('r.\`', COLUMN_NAME, '\`'))
                    ORDER BY ORDINAL_POSITION) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '$DB' AND TABLE_NAME = 'rules_rule'" </dev/null)
awards=""
[ "${COPY_AWARDS:-0}" = 1 ] && awards="UPDATE $DB.events_eventconfiguration tc JOIN cfgmap m ON m.target_id = tc.id SET tc.award = m.award;"
q rgg_cfg <<SQL
CREATE TEMPORARY TABLE cfgmap AS
  SELECT sc.id AS source_id, tc.id AS target_id, sc.award AS award
  FROM rgg_cfg.events_eventconfiguration sc
  JOIN rgg_cfg.events_eventtype st ON st.id = sc.event_type_id
  JOIN $DB.events_eventtype tt ON tt.name = st.name
  JOIN $DB.events_eventconfiguration tc ON tc.event_type_id = tt.id;
START TRANSACTION;
INSERT INTO $DB.rules_rule ($(cols rules_rule)) SELECT $rule_sel FROM rgg_cfg.rules_rule r LEFT JOIN cfgmap m ON m.source_id = r.event_configuration_id;
INSERT INTO $DB.badges_badge ($(cols badges_badge)) SELECT $(cols badges_badge) FROM rgg_cfg.badges_badge;
INSERT INTO $DB.badges_badge_rules ($(cols badges_badge_rules)) SELECT $(cols badges_badge_rules) FROM rgg_cfg.badges_badge_rules;
INSERT INTO $DB.statuses_status ($(cols statuses_status)) SELECT $(cols statuses_status) FROM rgg_cfg.statuses_status;
$awards
COMMIT;
SQL
mkdir -p "$MEDIA"
tar -C "$MEDIA" -xzf "$EXP/media.tgz"

# ---- checks ----
bad=0
for t in $CONFIG; do
  a=$(q -N -e "SELECT COUNT(*) FROM rgg_cfg.$t" </dev/null); b=$(q -N -e "SELECT COUNT(*) FROM $DB.$t" </dev/null)
  echo "   $t: export $a, now $b"; [ "$a" = "$b" ] || bad=1
done
wrong=$(q -N -e "SELECT COUNT(*) FROM rgg_cfg.rules_rule sr
  JOIN $DB.rules_rule tr ON tr.id = sr.id
  LEFT JOIN rgg_cfg.events_eventconfiguration sc ON sc.id = sr.event_configuration_id LEFT JOIN rgg_cfg.events_eventtype st ON st.id = sc.event_type_id
  LEFT JOIN $DB.events_eventconfiguration tc ON tc.id = tr.event_configuration_id LEFT JOIN $DB.events_eventtype tt ON tt.id = tc.event_type_id
  WHERE NOT (st.name <=> tt.name)" </dev/null)
echo "   rules pointing at a different event type than on the source: $wrong"; [ "$wrong" = 0 ] || bad=1
absent=$(cd "$MEDIA" && while read -r f; do [ -f "$f" ] || echo "$f"; done < "$EXP/media-files.txt" | wc -l)
echo "   image files missing after the copy: $absent"; [ "$absent" = 0 ] || bad=1
q -e "DROP DATABASE rgg_cfg" </dev/null
[ "$bad" = 0 ] || { echo "GAMMA CONFIG IMPORT CHECK FAILED"; exit 1; }
echo "GAMMA CONFIG IMPORT DONE"
