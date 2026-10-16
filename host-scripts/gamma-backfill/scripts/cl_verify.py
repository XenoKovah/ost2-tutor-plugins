#!/usr/bin/env python3
"""After a --commit run: each learner's points changed by exactly the bonus of the streak badges this run
granted them (per the --verbose-users lines of the commit logs), and no streak counter changed.
Usage: cl_verify.py OUTDIR   (OUTDIR holds users-before.tsv, users-after.tsv, commit_*.log)"""
import collections, glob, re, sys

BONUS = {5: 10, 10: 10, 20: 25, 30: 50}
d = sys.argv[1]


def users(f):
    rows = {}
    for line in open(f):
        p = line.rstrip('\n').split('\t')
        rows[p[0]] = p[1:]  # points, current_streak, last_active_date, current_weekday_streak, last_weekday_active_date
    return rows


b, a = users(d + '/users-before.tsv'), users(d + '/users-after.tsv')
exp, grants = collections.Counter(), 0
for f in glob.glob(d + '/commit_*.log'):
    for m in re.finditer(r'^(\S+): \d+ active days; (.*)$', open(f).read(), re.M):
        for part in m.group(2).split(', '):
            kind_days, outcome = part.rsplit('=', 1)
            if outcome == 'granted':
                exp[m.group(1)] += BONUS[int(kind_days.split(':')[1])]
                grants += 1
pts_bad = sorted(u for u in set(a) | set(b) if int(a.get(u, ['0'])[0]) - int(b.get(u, ['0'])[0]) != exp[u])
ctr_bad = sorted(u for u in set(a) & set(b) if a[u][1:] != b[u][1:])
print('grants %d to %d learners; expected points %+d; actual %+d; learners whose points change != their new streak badges: %d %s; streak counters changed: %d %s' % (
    grants, len(exp), sum(exp.values()), sum(int(v[0]) for v in a.values()) - sum(int(v[0]) for v in b.values()),
    len(pts_bad), pts_bad[:5], len(ctr_bad), ctr_bad[:5]))
print('CL_VERIFY', 'OK' if not pts_bad and not ctr_bad else 'MISMATCH')
