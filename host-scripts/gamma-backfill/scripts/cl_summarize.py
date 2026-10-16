#!/usr/bin/env python3
"""Sum backfill_continuous_learning shard logs: per-badge outcomes, totals, shard times.
Usage: cl_summarize.py shard_0.log shard_1.log ...   The last line is SUMMARY_JSON {...}."""
import collections, json, re, sys

BONUS = {5: 10, 10: 10, 20: 25, 30: 50}  # streak days -> Badge.points (users.continuous_learning.STREAK_MILESTONES)
out = collections.Counter()
scanned = reached = 0
secs, modes, ignoring = [], set(), set()
for path in sys.argv[1:]:
    t = open(path).read()
    for m in re.finditer(r'^(DRY-RUN|COMMITTED)\S*(?: \(nothing written\))?: shard \d+/\d+; scanned (\d+) learners, (\d+) reached', t, re.M):
        modes.add(m.group(1)); scanned += int(m.group(2)); reached += int(m.group(3))
    for m in re.finditer(r'^\s+(\d+)-(day|weekday)-streak\s+\(\+\d+ pts\): (.*)$', t, re.M):
        for kv in m.group(3).split(', '):
            k, n = kv.split('=')
            out[(m.group(2), int(m.group(1)), k)] += int(n)
    secs += [float(x) for x in re.findall(r'^SHARD_SECONDS ([\d.]+)', t, re.M)]
    ignoring |= set(re.findall(r'ignoring (\d+) listed events', t))
assert len(modes) == 1 and len(secs) == len(sys.argv) - 1, (modes, len(secs))
outcomes = sorted({k for (_, _, k) in out})
print('%s: %d shards; scanned %d learners, %d reached a milestone; shard seconds %s; ignored events %s' % (
    modes.pop(), len(secs), scanned, reached, ' '.join('%.1f' % s for s in secs), ','.join(sorted(ignoring))))
print('%-18s %5s %s' % ('badge', 'bonus', ''.join('%14s' % o for o in outcomes)))
tot, pts = collections.Counter(), 0
for kind in ('day', 'weekday'):
    for d in (5, 10, 20, 30):
        for o in outcomes:
            tot[o] += out[(kind, d, o)]
        pts += (out[(kind, d, 'granted')] + out[(kind, d, 'would_grant')]) * BONUS[d]
        print('%-18s %5s %s' % ('%d-%s-streak' % (d, kind), '+%d' % BONUS[d], ''.join('%14d' % out[(kind, d, o)] for o in outcomes)))
print('%-18s %5s %s' % ('total', '', ''.join('%14d' % tot[o] for o in outcomes)))
print('bonus points (granted or would grant): %+d' % pts)
print('SUMMARY_JSON ' + json.dumps({'scanned': scanned, 'reached': reached, 'secs': secs, 'bonus_points': pts, 'totals': dict(tot),
                                    'per_badge': {'%d-%s' % (d, k): {o: out[(k, d, o)] for o in outcomes} for k in ('day', 'weekday') for d in (5, 10, 20, 30)}}))
