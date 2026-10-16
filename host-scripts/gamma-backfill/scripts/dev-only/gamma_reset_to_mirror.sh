#!/usr/bin/env bash
# dev.ost2.fyi ONLY. Reset Gamma's DATA to the 2026-09-30 p snapshot while keeping dev's CURRENT
# accomplishment config (badges, rules, badge-rule links, statuses, per-event points) and
# re-applying manual (rule-less) badge grants. Gives every backfill run the same clean start.
# Usage: [SKIP_MANUAL_BADGE_IDS=71,72] bash gamma_reset_to_mirror.sh
#   SKIP_MANUAL_BADGE_IDS: manual badges whose current grants are NOT re-applied (e.g. the list-driven
#   badges of BACKFILLS.md section 3, so a validation run re-grants them from the lists).
set -Eeuo pipefail
trap 'echo "$(date -u +%FT%TZ) FAILED at line $LINENO"' ERR
[ "$(tutor config printvalue LMS_HOST)" = "dev.ost2.fyi" ] || { echo "refusing: this is not dev"; exit 1; }
for u in gamma-backfill gamma-backfill-uts; do
  if systemctl is-active --quiet "$u"; then echo "refusing: $u is running"; exit 1; fi
done
W=$HOME/p2dev-20260930
P=$W/from-p
R=$(tutor config printroot)
K=$W/reset-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$K"
export MYSQL_PWD=$(tutor config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot --default-character-set=utf8mb4 "$@"; }
DUMP="mysqldump -uroot --single-transaction --set-gtid-purged=OFF --hex-blob --no-tablespaces --default-character-set=utf8mb4"
CFG="badges_badge badges_badge_rules rules_rule statuses_status events_eventtype events_eventconfiguration"
log(){ echo "$(date -u +%FT%TZ) $*"; }

log "1/9 back up gamma; export current config + manual grants -> $K"
docker exec -e MYSQL_PWD tutor_local-mysql-1 $DUMP --quick rgg </dev/null | zstd -q -3 > "$K/rgg-before-reset.sql.zst"
docker exec -e MYSQL_PWD tutor_local-mysql-1 $DUMP rgg $CFG </dev/null > "$K/config.sql"
sql -N -e "SELECT a.object_id, u.user_uid FROM rgg.achievements_achievement a
  JOIN rgg.users_gammauser u ON u.id=a.user_id
  JOIN rgg.django_content_type ct ON ct.id=a.content_type_id AND ct.app_label='badges' AND ct.model='badge'
  WHERE a.completed_at IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM rgg.badges_badge_rules br WHERE br.badge_id=a.object_id)
    ${SKIP_MANUAL_BADGE_IDS:+AND a.object_id NOT IN ($SKIP_MANUAL_BADGE_IDS)}
  ORDER BY a.id" </dev/null > "$K/manual-grants.tsv"
log "   manual grants to re-apply: $(wc -l < "$K/manual-grants.tsv")${SKIP_MANUAL_BADGE_IDS:+ (not re-applying badges $SKIP_MANUAL_BADGE_IDS)}"
mkdir -p "$K/cursors"
mv "$R/data/lms/gamma_backfill/"* "$K/cursors/" 2>/dev/null || true
rm -f "$HOME/gamma-backfill/COMPLETE-using-the-site"

log "2/9 stop gamma"
tutor local stop rgg rgg-worker rgg-beat

log "3/9 load p's gamma snapshot (rgg section of the 2026-09-30 dump)"
sql -e "DROP DATABASE IF EXISTS rgg" </dev/null
{ echo "SET SESSION sql_log_bin=0;"
  zstd -dc "$P/p-mysql-openedx-rgg.sql.zst" \
    | awk 'BEGIN{p=1} /^-- Current Database: `openedx`/{p=0} /^-- Current Database: `rgg`/{p=1} p'
} | sql
log "   loaded: $(sql -N -e 'SELECT COUNT(*) FROM rgg.events_event' </dev/null) events"

log "4/9 re-key app client 'main' to dev's key/secret; flush gamma's redis db (3)"
KEY=$(tutor config printvalue RGG_DEFAULT_APP_KEY) SECRET=$(tutor config printvalue RGG_DEFAULT_APP_SECRET) \
  docker exec -e MYSQL_PWD -e KEY -e SECRET tutor_local-mysql-1 sh -c 'mysql -uroot rgg -N -e "UPDATE core_appclient SET \`key\`=\"$KEY\", secret=\"$SECRET\" WHERE name=\"main\"; SELECT ROW_COUNT()"' </dev/null
docker exec tutor_local-redis-1 redis-cli -n 3 FLUSHDB </dev/null

log "5/9 tutor local do init --limit=rgg (migrate to current code; site; leaderboards)"
tutor local do init --limit=rgg </dev/null

log "6/9 import current config (rules' event configurations mapped by event-type NAME)"
sql -e "DROP DATABASE IF EXISTS rgg_cfg; CREATE DATABASE rgg_cfg" </dev/null
{ echo "SET SESSION sql_log_bin=0;"; cat "$K/config.sql"; } | sql rgg_cfg
unmapped=$(sql -N -e "SELECT COUNT(*) FROM rgg_cfg.events_eventtype dt LEFT JOIN rgg.events_eventtype pt ON pt.name=dt.name WHERE pt.id IS NULL" </dev/null)
[ "$unmapped" = 0 ] || { log "$unmapped event types missing on target"; exit 1; }
cols(){ sql -N -e "SELECT GROUP_CONCAT(CONCAT('\`',column_name,'\`') ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='rgg_cfg' AND table_name='$1'" </dev/null; }
rule_sel=$(sql -N -e "SELECT GROUP_CONCAT(IF(column_name='event_configuration_id','m.p_id',CONCAT('r.\`',column_name,'\`')) ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='rgg_cfg' AND table_name='rules_rule'" </dev/null)
sql rgg_cfg <<SQL
SET SESSION sql_log_bin=0;
START TRANSACTION;
CREATE TEMPORARY TABLE cfgmap AS
  SELECT dc.id AS dev_id, pc.id AS p_id
  FROM rgg_cfg.events_eventconfiguration dc
  JOIN rgg_cfg.events_eventtype dt ON dt.id = dc.event_type_id
  JOIN rgg.events_eventtype pt ON pt.name = dt.name
  JOIN rgg.events_eventconfiguration pc ON pc.event_type_id = pt.id;
DELETE FROM rgg.badges_badge_rules;
DELETE FROM rgg.rules_rule;
DELETE FROM rgg.badges_badge;
DELETE FROM rgg.statuses_status;
INSERT INTO rgg.rules_rule ($(cols rules_rule)) SELECT $rule_sel FROM rgg_cfg.rules_rule r LEFT JOIN cfgmap m ON m.dev_id = r.event_configuration_id;
INSERT INTO rgg.badges_badge ($(cols badges_badge)) SELECT $(cols badges_badge) FROM rgg_cfg.badges_badge;
INSERT INTO rgg.badges_badge_rules ($(cols badges_badge_rules)) SELECT $(cols badges_badge_rules) FROM rgg_cfg.badges_badge_rules;
INSERT INTO rgg.statuses_status ($(cols statuses_status)) SELECT $(cols statuses_status) FROM rgg_cfg.statuses_status;
UPDATE rgg.events_eventconfiguration pc
  JOIN rgg.events_eventtype pt ON pt.id = pc.event_type_id
  JOIN rgg_cfg.events_eventtype dt ON dt.name = pt.name
  JOIN rgg_cfg.events_eventconfiguration dc ON dc.event_type_id = dt.id
  SET pc.award = dc.award;
COMMIT;
SQL
mismatch=$(sql -N -e "SELECT COUNT(*) FROM rgg_cfg.rules_rule dr LEFT JOIN rgg.rules_rule pr ON pr.id=dr.id LEFT JOIN rgg_cfg.events_eventconfiguration dc ON dc.id=dr.event_configuration_id LEFT JOIN rgg_cfg.events_eventtype dt ON dt.id=dc.event_type_id LEFT JOIN rgg.events_eventconfiguration pc ON pc.id=pr.event_configuration_id LEFT JOIN rgg.events_eventtype pt ON pt.id=pc.event_type_id WHERE pr.id IS NULL OR NOT (dt.name <=> pt.name) OR NOT (dr.action <=> pr.action) OR NOT (dr.filters <=> pr.filters)" </dev/null)
[ "$mismatch" = 0 ] || { log "$mismatch rules differ after import"; exit 1; }
for t in badges_badge badges_badge_rules rules_rule statuses_status; do
  a=$(sql -N -e "SELECT COUNT(*) FROM rgg_cfg.$t" </dev/null); b=$(sql -N -e "SELECT COUNT(*) FROM rgg.$t" </dev/null)
  [ "$a" = "$b" ] || { log "$t: exported $a, imported $b"; exit 1; }
done
sql -e "DROP DATABASE rgg_cfg" </dev/null

log "7/9 start gamma; reset its admin password to dev's"
tutor local start -d rgg rgg-worker rgg-beat </dev/null
for i in $(seq 1 60); do docker exec tutor_local-rgg-1 true 2>/dev/null && break; sleep 2; done
U=$(tutor config printvalue RGG_DJANGO_ADMIN_USER) PW=$(tutor config printvalue RGG_DJANGO_ADMIN_PASS) \
  docker exec -e U -e PW tutor_local-rgg-1 sh -c 'cd /rgg/gamma && /rgg/venv/bin/python manage.py shell -c "import os; from django.contrib.auth.models import User; u = User.objects.get(username=os.environ[\"U\"]); u.set_password(os.environ[\"PW\"]); u.save(); print(\"admin password set\")"' </dev/null

log "8/9 re-apply manual grants (Badge.award_to_user; skips users missing from p's gamma)"
docker cp "$K/manual-grants.tsv" tutor_local-rgg-1:/tmp/manual-grants.tsv
docker exec -e RGG_SKIP_POINTS_REFRESH=true tutor_local-rgg-1 sh -c 'cd /rgg/gamma && /rgg/venv/bin/python manage.py shell -c "
from django.db.models import F
from achievements.models import Achievement
from badges.models import Badge
from users.models import GammaUser
granted, missing = 0, []
for line in open(\"/tmp/manual-grants.tsv\"):
    badge_id, user_uid = line.rstrip(\"\n\").split(\"\t\")
    user = GammaUser.objects.filter(user_uid=user_uid).first()
    if user is None:
        missing.append(user_uid)
        continue
    if Badge.objects.get(id=int(badge_id)).award_to_user(user):
        granted += 1
        Achievement.objects.filter(user=user, object_id=int(badge_id), content_type__model=\"badge\", completed_at__isnull=False,
                                   notification_seen_at__isnull=True).update(notification_seen_at=F(\"completed_at\"))
print(\"manual grants re-applied:\", granted, \"| users missing:\", missing)
"' </dev/null

log "9/9 verify"
sql -e "SELECT (SELECT COUNT(*) FROM rgg.events_event) events, (SELECT COUNT(*) FROM rgg.achievements_achievement) achievements,
  (SELECT COUNT(*) FROM rgg.badges_badge) badges, (SELECT COUNT(*) FROM rgg.badges_badge WHERE is_active=1) active_badges,
  (SELECT COUNT(*) FROM rgg.rules_rule) rules, (SELECT COUNT(*) FROM rgg.statuses_status) statuses,
  (SELECT SUM(points) FROM rgg.users_gammauser) total_points" </dev/null
log "RESET DONE (backup + exported config in $K)"
