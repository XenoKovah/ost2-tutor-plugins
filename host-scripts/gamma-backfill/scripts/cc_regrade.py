#!/usr/bin/env python3
"""Course Completion tier regrade: compute every (learner, class) tier from LMS data. Read-only.

Spec: RGG/COURSE_COMPLETION_TIER_TIMING.md sections 3 and 3a. For each (learner, class) holding a
downloadable certificate, the first rule that applies wins:
  1. beta override -> Gold: a certificate of the class is on a run where the learner has an active
     allowlist entry, and the (learner, class) pair is in beta_completion_pairs.tsv (derived from
     COMPLETED_BETA_CLASS_CANDIDATES.md by cc_beta_pairs.py);
     a pair Veronica decided (source "decision ...", from manual/beta_completion_decisions.tsv) is Gold
     even when its certificate isn't allowlisted, e.g. an earned certificate with no clicks before it;
  2. click timing: first "Mark as complete" click in the class (any run, courseware_studentmodule
     type 'done', state done:true, `created` = first click) -> the last click in the certified run
     before the certificate. Gold <= 2 weeks < Silver <= 4 weeks < Bronze <= 12 weeks < Plain;
  3. no click before the certificate -> Plain.
The class's certificate is its earliest downloadable one. Inputs: cc_extract.sh output.
Writes OUTDIR/plan.tsv (one row per learner-class) and prints the report.
Usage: cc_regrade.py EXTRACT_DIR beta_completion_pairs.tsv OUTDIR"""
import collections, csv, datetime as dt, json, os, re, sys

X, PAIRS, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
WEEK = dt.timedelta(weeks=1)
TIERS = ('gold', 'silver', 'bronze', 'plain')


def tsv(name):
    path = os.path.join(X, name)
    if not os.path.getsize(path):
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE))


def ts(s):
    return dt.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')


def short(course_id):
    return course_id.split(':', 1)[1].split('+', 1)[1]


def band(elapsed):
    if elapsed <= 2 * WEEK:
        return 'gold'
    if elapsed <= 4 * WEEK:
        return 'silver'
    if elapsed <= 12 * WEEK:
        return 'bronze'
    return 'plain'


# ---- classes: the four tier badges per class, and every run in their course filter ----
classes, run_class = {}, {}
for r in tsv('tiers.tsv'):
    f = json.loads(r['filters'])
    runs = tuple(sorted(f['course'] if isinstance(f['course'], list) else [f['course']]))
    tier = r['category'].split('/', 1)[1].strip().split(' ')[0].lower()
    c = classes.setdefault(r['title'], {'runs': runs, 'tiers': {}})
    assert c['runs'] == runs, ('tiers of a class disagree on runs', r['title'])
    c['tiers'][tier] = {'badge_id': int(r['badge_id']), 'points': int(r['points']), 'window': f.get('completion_window')}
    for run in runs:
        assert run_class.get(run, r['title']) == r['title'], ('run in two classes', run)
        run_class[run] = r['title']
assert all(set(c['tiers']) == set(TIERS) for c in classes.values()), 'every class has 4 tiers'

certs = tsv('certs.tsv')
allow = {(r['user_id'], r['course_id']): r['notes'] for r in tsv('allowlist.tsv')}
clicks = collections.defaultdict(list)  # (user_id, course_id) -> [first-click times]
for r in tsv('clicks.tsv'):
    clicks[(r['user_id'], r['course_id'])].append(ts(r['created']))
users = {r['username']: r for r in tsv('candidate_users.tsv')}
holdings = collections.defaultdict(list)  # username -> [(badge_id, paid)]
for r in tsv('holdings.tsv'):
    if r['completed_at'] != 'NULL':
        holdings[r['user_uid']].append((int(r['badge_id']), None if r['completion_points_paid'] == 'NULL' else int(r['completion_points_paid'])))
badge_tier = {t['badge_id']: (title, tier) for title, c in classes.items() for tier, t in c['tiers'].items()}
site = tsv('site_points.tsv')[0]

# ---- beta pairs: p username + p run -> (user_id, class) ----
runs_by_short = collections.defaultdict(set)
for run in set(run_class) | {r['course_id'] for r in certs} | {k[1] for k in allow}:
    runs_by_short[short(run)].add(run)
lower = collections.Counter(u.lower() for u in users)
pairs, flag_unmatched = {}, []
for r in csv.DictReader(open(PAIRS), delimiter='\t'):
    u = users.get(r['p_username'])
    runs = runs_by_short.get(r['p_run'], set())
    if u is None or lower[r['p_username'].lower()] != 1:
        flag_unmatched.append((r['p_username'], r['p_run'], 'no p account' if u is None else 'several accounts'))
        continue
    if len(runs) != 1 or next(iter(runs)) not in run_class:
        flag_unmatched.append((r['p_username'], r['p_run'], 'run is in no tiered class' if runs else 'unknown run'))
        continue
    pairs.setdefault((u['id'], run_class[next(iter(runs))]), []).append(r)

# ---- per (learner, class) ----
by_lc = collections.defaultdict(list)
untiered = collections.Counter()
for c in certs:
    cls = run_class.get(c['course_id'])
    if cls is None:
        untiered[c['course_id']] += 1
        continue
    by_lc[(c['user_id'], cls)].append(c)

plan, reasons = [], collections.Counter()
flag_not_allowlisted, flag_beta_note_not_listed, flag_multi = [], [], []
per_cert = collections.Counter()
for (uid, cls), cs in sorted(by_lc.items()):
    cs.sort(key=lambda c: c['created_date'])
    cert = cs[0]
    T = ts(cert['created_date'])
    runs = classes[cls]['runs']
    before = sorted(t for run in runs for t in clicks.get((uid, run), []) if t <= T)
    in_run = [t for t in clicks.get((uid, cert['course_id']), []) if t <= T]
    allowlisted = [c for c in cs if (uid, c['course_id']) in allow]
    beta = (uid, cls) in pairs
    decided = beta and any(r['source'].startswith('decision') for r in pairs[(uid, cls)])
    if len(cs) > 1:
        flag_multi.append((cert['username'], cls, [c['course_id'].split('+', 1)[1] for c in cs]))
    timing, start, end = None, None, None
    if before:
        start, end = before[0], (max(in_run) if in_run else before[-1])
        timing = band(end - start)
    if beta and allowlisted:
        tier, reason = 'gold', 'beta override'
    elif decided:
        tier, reason = 'gold', 'decision'
    elif timing:
        tier, reason = timing, 'click timing' + ('' if in_run else ' (no click in the certified run: end = last click in any run)')
    else:
        tier, reason = 'plain', 'no click before the certificate'
    if beta and not allowlisted and not decided:
        flag_not_allowlisted.append((cert['username'], cls, cert['course_id'], timing or 'plain'))
    notes = ' | '.join(allow[(uid, c['course_id'])] for c in allowlisted)
    if allowlisted and re.search('beta', notes, re.I) and not beta:
        flag_beta_note_not_listed.append((cert['username'], cls, notes[:80], tier))
    reasons[(tier, reason.split(' (')[0])] += 1
    target = classes[cls]['tiers'][tier]
    held = [(b, p) for b, p in holdings.get(cert['username'], []) if badge_tier[b][0] == cls]
    if not held:
        action, topup = 'new', target['points']
    elif len(held) > 1:
        action, topup = 'conflict: holds %d tiers' % len(held), 0
    elif held[0][0] == target['badge_id']:
        action, topup = 'keep', 0
    else:
        paid = held[0][1] if held[0][1] is not None else classes[cls]['tiers'][badge_tier[held[0][0]][1]]['points']
        action, topup = 'move %s->%s' % (badge_tier[held[0][0]][1], tier), max(0, target['points'] - paid)
    plan.append({
        'user_id': uid, 'username': cert['username'], 'class': cls, 'tier': tier, 'badge_id': target['badge_id'],
        'points': target['points'], 'reason': reason, 'cert_id': cert['cert_id'], 'cert_run': cert['course_id'],
        'cert_created': cert['created_date'][:19], 'first_click': start and start.isoformat(' '),
        'end_click': end and end.isoformat(' '), 'elapsed_days': '' if start is None else '%.2f' % ((end - start).total_seconds() / 86400),
        'held': ','.join(badge_tier[b][1] for b, p in held), 'action': action, 'topup': topup,
        'class_badge_ids': ','.join(str(classes[cls]['tiers'][t]['badge_id']) for t in TIERS),
    })

# per-certificate measure, to check against the spec's 2026-10-01 numbers (section 3)
for c in certs:
    cls = run_class.get(c['course_id'])
    runs = classes[cls]['runs'] if cls else (c['course_id'],)
    T = ts(c['created_date'])
    before = sorted(t for run in runs for t in clicks.get((c['user_id'], run), []) if t <= T)
    in_run = [t for t in clicks.get((c['user_id'], c['course_id']), []) if t <= T]
    if before:
        per_cert['with a click'] += 1
        per_cert['A ' + band(before[-1] - before[0])] += 1
        per_cert['B ' + band((max(in_run) if in_run else before[-1]) - before[0])] += 1
    else:
        per_cert['no click'] += 1
        per_cert['no click, allowlisted'] += (c['user_id'], c['course_id']) in allow

with open(os.path.join(OUT, 'plan.tsv'), 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(plan[0]), delimiter='\t', lineterminator='\n')
    w.writeheader()
    w.writerows(plan)

# ---- report ----
P = lambda n: format(n, ',')
tc = collections.Counter(p['tier'] for p in plan)
print('Course Completion tier regrade: DRY RUN (nothing written)')
print('site: %s gamma users, total points %s at %s UTC (APPLY asserts this)' % (P(int(site['users'])), P(int(site['total_points'])), site['at_utc']))
print('classes %d (tier badges %d); downloadable certificates %s; learner-classes %s; certificates on untiered runs %d (%s)' % (
    len(classes), 4 * len(classes), P(len(certs)), P(len(plan)), sum(untiered.values()), ', '.join('%s %d' % (short(k), v) for k, v in untiered.most_common())))
print('\nTarget tiers:')
for t in TIERS:
    print('  %-6s %5s   %s' % (t, P(tc[t]), '; '.join('%s %s' % (r, P(n)) for (tt, r), n in sorted(reasons.items()) if tt == t)))
acts = collections.Counter(p['action'] for p in plan)
print('\nAgainst what gamma holds now: %s' % ', '.join('%s %s' % (a, P(n)) for a, n in acts.most_common()))
pts = collections.Counter()
for p in plan:
    pts[p['tier']] += p['topup']
print('Points to add (top-up only): %s = %s' % (P(sum(pts.values())), ' + '.join('%s %s' % (t, P(pts[t])) for t in TIERS)))
print('\nPer certificate (to check against the spec section 3, 2026-10-01): with a click %s, no click %s (allowlisted %s)' % (
    P(per_cert['with a click']), P(per_cert['no click']), P(per_cert['no click, allowlisted'])))
for k in 'AB':
    print('  end = last click %s: %s' % ('in any run' if k == 'A' else 'in the certified run', ', '.join('%s %s' % (t, P(per_cert[k + ' ' + t])) for t in TIERS)))
print('\nFLAGGED (section 3a: report, do not decide)')
print('1. Candidates whose certificate here is NOT allowlisted (fall through to timing): %d' % len(flag_not_allowlisted))
for x in flag_not_allowlisted:
    print('     %s | %s | %s -> %s' % x)
nocert = []
for (uid, cls), rows in sorted(pairs.items()):
    if (uid, cls) not in by_lc:
        nocert.append((rows[0]['p_username'], cls))
print('2. Candidates with NO downloadable certificate in that class here: %d' % len(nocert))
for x in nocert:
    print('     %s | %s' % x)
print('3. Allowlisted certificates with a beta note, NOT in the candidates file: %d' % len(flag_beta_note_not_listed))
for x in flag_beta_note_not_listed:
    print('     %s | %s | note "%s" -> %s' % x)
print('4. Candidates that cannot be matched, or match several accounts: %d' % len(flag_unmatched))
for x in flag_unmatched:
    print('     %s | %s | %s' % x)
print('5. Matched by username (the file has no emails): all %d pairs (%d learners). The usernames come from p\'s own allowlist rows, so on p/dev they are exact identities.' % (
    sum(len(v) for v in pairs.values()), len({k[0] for k in pairs})))
print('6. Learner-classes with several downloadable certificates (earliest used): %d' % len(flag_multi))
for x in flag_multi[:20]:
    print('     %s | %s | %s' % (x[0], x[1], ', '.join(x[2])))
