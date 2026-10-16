#!/usr/bin/env bash
# Read-only extraction for the Ignominious! backfills (ignominious.py): SELECT only.
# The classes are the 33 Course Completion classes: every run listed by their tier rules.
# Usage: bash ig_extract.sh OUTDIR
set -Eeuo pipefail
OUT=${1:?usage: $0 OUTDIR}; mkdir -p "$OUT"
export MYSQL_PWD=$($HOME/tutor-venv/bin/tutor config printvalue MYSQL_ROOT_PASSWORD)
q(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot --batch --default-character-set=utf8mb4 "$@"; }
q rgg > "$OUT/classes.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT DISTINCT b.title, r.filters FROM badges_badge b JOIN badges_badge_rules br ON br.badge_id = b.id
JOIN rules_rule r ON r.id = br.rule_id WHERE b.category LIKE 'Course Completion /%';
SQL
RUNS=$(python3 -c "
import csv, json, sys
runs = set()
for r in csv.DictReader(open(sys.argv[1]), delimiter='\t', quoting=csv.QUOTE_NONE):
    c = json.loads(r['filters'])['course']
    runs.update(c if isinstance(c, list) else [c])
print(','.join(\"'%s'\" % x for x in sorted(runs)))" "$OUT/classes.tsv")
q openedx > "$OUT/enrollments.tsv" <<SQL
SET SESSION TRANSACTION READ ONLY;
SELECT e.user_id, e.course_id, e.created, e.is_active, UTC_TIMESTAMP() AS q_now_utc
FROM student_courseenrollment e WHERE e.course_id IN ($RUNS);
SQL
q openedx > "$OUT/certificates.tsv" <<SQL
SET SESSION TRANSACTION READ ONLY;
SELECT user_id, course_id FROM certificates_generatedcertificate WHERE status = 'downloadable' AND course_id IN ($RUNS);
SQL
q openedx > "$OUT/clicks.tsv" <<SQL
SET SESSION TRANSACTION READ ONLY;
SELECT student_id AS user_id, course_id, COUNT(*) AS clicks, MIN(created) AS first_click
FROM courseware_studentmodule
WHERE module_type = 'done' AND state LIKE '%"done": true%' AND course_id IN ($RUNS)
GROUP BY student_id, course_id;
SQL
q openedx > "$OUT/grades.tsv" <<SQL
SET SESSION TRANSACTION READ ONLY;
SELECT user_id, course_id, percent_grade FROM grades_persistentcoursegrade WHERE percent_grade > 0 AND course_id IN ($RUNS);
SQL
q openedx > "$OUT/users.tsv" <<SQL
SET SESSION TRANSACTION READ ONLY;
SELECT u.id, u.username, u.is_active, u.is_staff, u.is_superuser
FROM auth_user u WHERE u.id IN (SELECT DISTINCT user_id FROM student_courseenrollment WHERE course_id IN ($RUNS));
SQL
q openedx > "$OUT/course_team.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT DISTINCT user_id FROM student_courseaccessrole WHERE role IN ('staff', 'instructor');
SQL
wc -l "$OUT"/*.tsv | sed "s#$OUT/##"
