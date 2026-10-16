#!/usr/bin/env python3
"""Derive the beta-completion (learner, class) pairs for the Course Completion Gold override
(COURSE_COMPLETION_TIER_TIMING.md section 3a) from COMPLETED_BETA_CLASS_CANDIDATES.md, the
only authority on who completed a beta.

One row of its section 3 = one active certificate-allowlist entry on p (learner x class run).
Pairs = rows classified "qualifies (beta note)" or "qualifies (beta grade >= 95%)", plus the
Arch1001 rows of the 8 learners in its `needs-decision-likely` block, added by decision on
2026-10-01. The file lists p usernames (no emails): it was built from p's own allowlist rows,
so on p (and on dev, a p mirror) a username is an exact identity, but every pair is matched
by username and reported as such.
Further pairs decided by Veronica are appended from the optional DECISIONS file
(manual/beta_completion_decisions.tsv: p_username, p_run, source), e.g. the 24 Arch1001 2021 launch
grants decided Gold on 2026-10-01.
Usage: cc_beta_pairs.py COMPLETED_BETA_CLASS_CANDIDATES.md [DECISIONS.tsv] > beta_completion_pairs.tsv"""
import re, sys

t = open(sys.argv[1]).read()
sec3 = t.split('\n## 3. ', 1)[1].split('\n## 4. ', 1)[0]
likely = re.search(r'^```needs-decision-likely\n(.*?)^```', t, re.S | re.M).group(1).split()
assert len(likely) == 8, likely
rows, seen = [], set()
for line in sec3.split('\n'):
    m = re.match(r'^\| (\d+) \| `([^`]+)` \| `([^`]+)` \| (.*?) \| (.*?) \| (.*?) \| (.*?) \| (.*?) \|$', line)
    if not m:
        continue
    n, user, run, note, beta_class, beta_grade, cls, reason = m.groups()
    cls = cls.replace('*', '').strip()
    if cls.startswith('qualifies'):
        source = cls
    elif user in likely and run.startswith('Arch1001_'):
        source = 'decision 2026-10-01 (needs-decision-likely, Arch1001)'
    else:
        continue
    rows.append((user, run, source, n))
assert {u for u, r, s, n in rows if s.startswith('decision')} == set(likely), 'every decided learner has an Arch1001 row'
if len(sys.argv) > 2:
    have = {(r[0], r[1]) for r in rows}
    for line in open(sys.argv[2]).read().splitlines()[1:]:
        user, run, source = line.split('\t')
        assert (user, run) not in have, ('decision duplicates a pair', user, run)
        rows.append((user, run, source, '-'))
print('p_username\tp_run\tsource\tsection3_row')
for r in rows:
    print('\t'.join(r))
print('pairs: %d (%d learners)' % (len(rows), len({r[0] for r in rows})), file=sys.stderr)
