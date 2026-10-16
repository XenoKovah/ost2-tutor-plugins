#!/usr/bin/env python3
"""
dev only, read-only: list the manual-badge grants dev's Gamma held BEFORE the 2026-09-30 p->dev mirror,
by streaming the pre-mirror mysqldump (never loading it anywhere):

    zstd -dc ~/p2dev-20260930/dev-backup/dev-mysql-openedx-rgg.sql.zst | python3 premirror_manual_grants.py OUTDIR

Parses only the `rgg` database's badges_badge, badges_badge_rules, achievements_achievement,
users_gammauser and django_content_type rows. Writes OUTDIR/badges.tsv (every badge, with its rule
count) and OUTDIR/holders.tsv (user_uid, badge_id, title, category, points, completed_at,
completion_points_paid, expires_at) for every completed achievement of a rule-less badge.
"""
import os
import re
import sys

TABLES = {'badges_badge', 'badges_badge_rules', 'achievements_achievement', 'users_gammauser', 'django_content_type'}
USE = re.compile(r'^USE `([^`]+)`;')
CREATE = re.compile(r'^CREATE TABLE `([^`]+)`')
COLUMN = re.compile(r'^\s+`([^`]+)` ')
INSERT = re.compile(r'^INSERT INTO `([^`]+)` VALUES ')
ESC = {'0': '\0', 'n': '\n', 'r': '\r', 't': '\t', 'Z': '\x1a', 'b': '\b'}


def tuples(text):
    """Yield the value tuples of one extended INSERT's VALUES list (MySQL dump quoting)."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != '(':
            i += 1
            continue
        i += 1
        row, cur, quoted = [], [], False
        while i < n:
            ch = text[i]
            if quoted:
                if ch == '\\':
                    i += 1
                    cur.append(ESC.get(text[i], text[i]))
                elif ch == "'":
                    quoted = False
                else:
                    cur.append(ch)
            elif ch == "'":
                quoted = True
                cur.append('')  # marks a string value even if empty
            elif ch in ',)':
                value = ''.join(cur)
                row.append(None if value == 'NULL' and not cur[:1] == [''] else value)
                cur = []
                if ch == ')':
                    break
            else:
                cur.append(ch)
            i += 1
        yield row
        i += 1


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    db, table, cols = None, None, {}
    rows = {t: [] for t in TABLES}
    for line in sys.stdin:
        m = USE.match(line)
        if m:
            db = m.group(1)
            continue
        if db != 'rgg':
            continue
        m = CREATE.match(line)
        if m:
            table = m.group(1)
            cols[table] = []
            continue
        if table and table in TABLES and line.startswith('  `'):
            c = COLUMN.match(line)
            if c and not line.lstrip().startswith(('PRIMARY', 'KEY', 'UNIQUE', 'CONSTRAINT')):
                cols[table].append(c.group(1))
            continue
        m = INSERT.match(line)
        if m and m.group(1) in TABLES:
            t = m.group(1)
            rows[t].extend(dict(zip(cols[t], r)) for r in tuples(line[m.end():]))
    ct = {r['id'] for r in rows['django_content_type'] if r['app_label'] == 'badges' and r['model'] == 'badge'}
    rule_count = {}
    for r in rows['badges_badge_rules']:
        rule_count[r['badge_id']] = rule_count.get(r['badge_id'], 0) + 1
    badges = {r['id']: r for r in rows['badges_badge']}
    users = {r['id']: r['user_uid'] for r in rows['users_gammauser']}
    with open(os.path.join(out, 'badges.tsv'), 'w') as f:
        f.write('badge_id\ttitle\tcategory\tpoints\tis_active\trules\n')
        for bid, b in sorted(badges.items(), key=lambda kv: int(kv[0])):
            f.write('%s\t%s\t%s\t%s\t%s\t%d\n' % (bid, b['title'], b['category'], b['points'], b['is_active'], rule_count.get(bid, 0)))
    n = 0
    with open(os.path.join(out, 'holders.tsv'), 'w') as f:
        f.write('user_uid\tbadge_id\ttitle\tcategory\tpoints\tcompleted_at\tcompletion_points_paid\texpires_at\n')
        for a in rows['achievements_achievement']:
            b = badges.get(a['object_id'])
            if a['content_type_id'] in ct and b and not rule_count.get(a['object_id']) and a.get('completed_at'):
                n += 1
                f.write('\t'.join(str(x) for x in (users.get(a['user_id'], '?'), a['object_id'], b['title'], b['category'], b['points'],
                                                   a['completed_at'], a.get('completion_points_paid'), a.get('expires_at'))) + '\n')
    print('rgg rows: %s; manual-badge grants: %d' % ({t: len(r) for t, r in rows.items()}, n))


if __name__ == '__main__':
    main()
