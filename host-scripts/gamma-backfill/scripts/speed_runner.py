#!/usr/bin/env python3
"""
Speed-runner! backfill selection (SPEED_RUNNER_BADGE_MEASUREMENT.md section 2; decided 2026-10-02).

For every (learner, course run) with LMS "Mark as complete" clicks:
  rushed     : a run of >= run_min consecutive clicks, each <= gap_sec after the previous one, with
               gap_sec / run_min and the course allowlist taken from the anti-gaming rule (Rule 39 on
               dev: 30 s / 10 / 33 courses). Courses outside the allowlist are never judged.
  engagement : from the LMS, not gamma (gamma has almost no video events). Videos watched
               (saved_video_position >= 60 s) and graded problems, counting only those first recorded
               BEFORE the start of the learner's longest rushed run. Engaged = they reach 25% of the
               course's checkable content (videos + problems, problems capped at 30; census.py's rule).
  CONFIRMED  : rushed and not engaged.
Left out: course-team members, site staff/superusers and inactive accounts (the Ignominious! rules), and
any (learner, class) the learner completed in beta or was granted Gold for by decision
(beta_completion_pairs.tsv, beta_completion_decisions.tsv): their live-run clicks are catch-up, not a
speed-run.
Outputs (OUTDIR):
  speed_runners.txt : one username per learner with >= 1 CONFIRMED run -> the Speed-runner! badge (once).
  demotions.tsv     : CONFIRMED runs whose class the learner holds at Gold -> moved to Plain, and the
                      Gold-Plain difference clawed back (Veronica, 2026-10-01).
  pairs.tsv, summary.txt : every judged run, and the report.
Usage: speed_runner.py EXTRACT_DIR beta_completion_pairs.tsv beta_completion_decisions.tsv OUTDIR
"""
import collections
import csv
import datetime as dt
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ignominious as ig  # noqa: E402  (class_of_run, excluded_users)

CONTENT_SHARE = 0.25
PROBLEM_CAP = 30
TIERS = ('gold', 'silver', 'bronze', 'plain')


def read(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE))


def ts(s):
    return dt.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')


def rush_profile(times, gap, run_min):
    """(rushed_blocks, longest_run, start of the longest run); process_anti_gaming's rush_profile + start."""
    if not times:
        return 0, 0, None
    rushed, longest, run, start, best_start = 0, 1, 1, 0, 0
    for i in range(1, len(times)):
        if (times[i] - times[i - 1]).total_seconds() <= gap:
            run += 1
        else:
            if run >= run_min:
                rushed += run
            run, start = 1, i
        if run > longest:
            longest, best_start = run, start
    if run >= run_min:
        rushed += run
    return rushed, longest, times[best_start]


def judge(times, videos, problems, content, allowlisted, gap, run_min):
    """Verdict for one learner's run: NOT_RUSHED | UNCHECKABLE | EXONERATED | CONFIRMED."""
    rushed, longest, start = rush_profile(sorted(times), gap, run_min)
    if longest < run_min:
        return dict(status='NOT_RUSHED', longest=longest, rushed=rushed)
    v_pre = sum(1 for t in videos.values() if t < start)
    p_pre = sum(1 for t in problems.values() if t < start)
    out = dict(longest=longest, rushed=rushed, start=start, v_pre=v_pre, p_pre=p_pre,
               v_any=len(videos), p_any=len(problems), content=content)
    if not allowlisted:
        out['status'] = 'UNCHECKABLE'
    else:
        engaged = content > 0 and v_pre + min(p_pre, PROBLEM_CAP) >= CONTENT_SHARE * content
        out['status'] = 'EXONERATED' if engaged else 'CONFIRMED'
    return out


def tier_of(category):
    """'Course Completion / Gold ...' -> 'gold'."""
    return category.split('/', 1)[1].strip().split(' ')[0].lower()


def class_badges(classes):
    """class title -> {tier: (badge_id, points)}."""
    out = collections.defaultdict(dict)
    for r in classes:
        out[r['title']][tier_of(r['category'])] = (int(r['badge_id']), int(r['points']))
    return out


def decided_pairs(rows, username_ids, run_class):
    """(user_id, class) pairs from a p_username/p_run file (runs given without the 'course-v1:<org>+' prefix)."""
    by_suffix = {run.split('+', 1)[1]: cls for run, cls in run_class.items()}
    pairs, unmatched = set(), []
    for r in rows:
        uid, cls = username_ids.get(r['p_username'].lower()), by_suffix.get(r['p_run'])
        if uid and cls:
            pairs.add((uid, cls))
        else:
            unmatched.append((r['p_username'], r['p_run']))
    return pairs, unmatched


def score(clicks, videos, problems, blocks, allow, gap, run_min):
    """{(user_id, course_id): verdict} for every run with clicks."""
    out = {}
    for key, times in clicks.items():
        b = blocks.get(key[1], {'video_blocks': 0, 'problem_blocks': 0})
        content = b['video_blocks'] + min(b['problem_blocks'], PROBLEM_CAP)
        out[key] = judge(times, videos.get(key, {}), problems.get(key, {}), content, key[1] in allow, gap, run_min)
    return out


def select(verdicts, run_class, excluded, exempt):
    """CONFIRMED runs that count: not an excluded learner, not a beta/decided-Gold (learner, class)."""
    kept, dropped = {}, collections.Counter()
    for (uid, cid), v in verdicts.items():
        if v['status'] != 'CONFIRMED':
            continue
        if uid in excluded:
            dropped['excluded learner (course team, staff, inactive)'] += 1
        elif (uid, run_class.get(cid)) in exempt:
            dropped['completed the class in beta / decided Gold'] += 1
        else:
            kept[(uid, cid)] = v
    return kept, dropped


def certified_run(certs, uid, runs):
    """The learner's earliest downloadable certificate among a class's runs (as cc_regrade.py picks)."""
    held = sorted((c['created'], c['course_id']) for c in certs.get(uid, ()) if c['course_id'] in runs)
    return held[0][1] if held else ''


def demotions(kept, run_class, usernames, held, badges, certs, class_runs):
    """One row per (learner, class) held at Gold with a CONFIRMED run in that class."""
    rows = {}
    for (uid, cid), v in sorted(kept.items(), key=lambda kv: kv[1]['start']):
        cls = run_class.get(cid)
        user = usernames.get(uid)
        if not cls or held.get((user, cls)) != 'gold' or (user, cls) in rows:
            continue
        (gold_id, gold_points), (plain_id, plain_points) = badges[cls]['gold'], badges[cls]['plain']
        rows[(user, cls)] = dict(username=user, cls=cls, gold_badge_id=gold_id, plain_badge_id=plain_id,
                                 gold_points=gold_points, plain_points=plain_points,
                                 cert_run=certified_run(certs, uid, class_runs[cls]), course_id=cid,
                                 longest_run=v['longest'], rushed_blocks=v['rushed'])
    return list(rows.values())


def main():
    extract, beta_pairs_path, decisions_path, out_dir = sys.argv[1:5]
    os.makedirs(out_dir, exist_ok=True)
    X = lambda name: read(os.path.join(extract, name))
    [rule] = X('rule39.tsv')
    policy = json.loads(rule['action'])['rgg_rushed_completion']
    gap, run_min = int(policy['gap_sec']), int(policy['run_min'])
    allow = set(json.loads(rule['filters'])['course'])
    classes = X('classes.tsv')
    run_class = ig.class_of_run([{'title': r['title'], 'filters': r['filters']} for r in classes])
    class_runs = collections.defaultdict(set)
    for run, cls in run_class.items():
        class_runs[cls].add(run)
    badges = class_badges(classes)
    users = X('users.tsv')
    usernames = {u['id']: u['username'] for u in users}
    username_ids = {u['username'].lower(): u['id'] for u in users}
    excluded = ig.excluded_users(users, X('course_team.tsv'))
    beta, beta_unmatched = decided_pairs(read(beta_pairs_path), username_ids, run_class)
    decided, decided_unmatched = decided_pairs(read(decisions_path), username_ids, run_class)
    held = {(r['user_uid'], r['title']): tier_of(r['category']) for r in X('tiers_held.tsv')}
    certs = collections.defaultdict(list)
    for c in X('certificates.tsv'):
        certs[c['user_id']].append(c)
    blocks = {b['course_id']: {k: int(b[k]) for k in ('done_units', 'video_blocks', 'problem_blocks')} for b in X('blocks.tsv')}

    def first_seen(name):
        out = collections.defaultdict(dict)
        for r in X(name):
            k, t = (r['student_id'], r['course_id']), ts(r['created'])
            out[k][r['module_id']] = min(out[k].get(r['module_id'], t), t)
        return out

    clicks = collections.defaultdict(list)
    for r in X('done.tsv'):
        clicks[(r['student_id'], r['course_id'])].append(ts(r['created']))
    verdicts = score(clicks, first_seen('videos.tsv'), first_seen('problems.tsv'), blocks, allow, gap, run_min)
    kept, dropped = select(verdicts, run_class, excluded, beta | decided)
    learners = sorted({usernames[uid] for uid, _ in kept}, key=str.lower)
    demote = demotions(kept, run_class, usernames, held, badges, certs, class_runs)

    with open(os.path.join(out_dir, 'speed_runners.txt'), 'w') as f:
        f.write(''.join(u + '\n' for u in learners))
    cols = ('username', 'cls', 'gold_badge_id', 'plain_badge_id', 'gold_points', 'plain_points', 'cert_run',
            'course_id', 'longest_run', 'rushed_blocks')
    with open(os.path.join(out_dir, 'demotions.tsv'), 'w') as f:
        f.write('\t'.join(cols) + '\n')
        for r in sorted(demote, key=lambda r: (r['username'].lower(), r['cls'])):
            f.write('\t'.join(str(r[c]) for c in cols) + '\n')
    with open(os.path.join(out_dir, 'pairs.tsv'), 'w') as f:
        f.write('username\tcourse_id\tclass\tstatus\tcounted\tlongest_run\trushed_blocks\tburst_start\tvideos_pre\tproblems_pre\tcontent\n')
        for (uid, cid), v in sorted(verdicts.items()):
            if v['status'] != 'NOT_RUSHED':
                f.write('\t'.join(str(x) for x in (usernames.get(uid, '?'), cid, run_class.get(cid, ''), v['status'], int((uid, cid) in kept),
                                                   v['longest'], v['rushed'], v.get('start', ''), v.get('v_pre', ''), v.get('p_pre', ''),
                                                   v.get('content', ''))) + '\n')

    P = lambda n: format(n, ',')
    status = collections.Counter(v['status'] for v in verdicts.values())
    S = []
    w = S.append
    w('Speed-runner! selection: gap %ds, run >= %d, %d allowlisted courses; engagement from the LMS before the burst' % (gap, run_min, len(allow)))
    w('runs with clicks %s (learners %s): CONFIRMED %s, EXONERATED %s, UNCHECKABLE (not allowlisted) %s, not rushed %s' % (
        P(len(verdicts)), P(len({k[0] for k in verdicts})), P(status['CONFIRMED']), P(status['EXONERATED']), P(status['UNCHECKABLE']), P(status['NOT_RUSHED'])))
    w('CONFIRMED runs left out: %s' % (dict(dropped) or 'none'))
    w("beta pairs matched %d (no clicks or no such run %d), decided-Gold pairs matched %d (no clicks or no such run %d)" % (
        len(beta), len(beta_unmatched), len(decided), len(decided_unmatched)))
    w('CONFIRMED runs counted %s -> Speed-runner! learners %s' % (P(len(kept)), P(len(learners))))
    tiers = collections.Counter(held.get((usernames[uid], run_class.get(cid)), 'no certificate') if run_class.get(cid) else 'not a Course Completion class'
                                for uid, cid in kept)
    w('counted runs by the tier the learner holds for that class: %s' % dict(tiers.most_common()))
    w('Gold -> Plain demotions: %d (learner, class), %d learners; Gold-Plain difference %s points; no certified run found for %d' % (
        len(demote), len({r['username'] for r in demote}), P(sum(r['gold_points'] - r['plain_points'] for r in demote)),
        sum(1 for r in demote if not r['cert_run'])))
    pc = collections.defaultdict(collections.Counter)
    for (uid, cid), v in verdicts.items():
        pc[cid][v['status']] += 1
        if (uid, cid) in kept:
            pc[cid]['counted'] += 1
    w('per course: CONFIRMED (counted) | EXONERATED')
    for cid, c in sorted(pc.items(), key=lambda kv: -kv[1]['counted']):
        if c['CONFIRMED']:
            w('  %-62s %4d (%4d) | %4d' % (cid.split(':', 1)[1], c['CONFIRMED'], c['counted'], c['EXONERATED']))
    random.seed(26)
    sample = random.sample(sorted(kept), min(10, len(kept)))
    w('10 sampled counted runs: learner | course | longest run | rushed blocks | videos/problems before burst | content | burst start')
    for uid, cid in sorted(sample, key=lambda k: usernames[k[0]].lower()):
        v = kept[(uid, cid)]
        w('  %s | %s | %d | %d | %d/%d | %d | %s' % (usernames[uid], cid.split('+', 1)[1], v['longest'], v['rushed'], v['v_pre'], v['p_pre'], v['content'], v['start']))
    open(os.path.join(out_dir, 'summary.txt'), 'w').write('\n'.join(S) + '\n')
    print('\n'.join(S))


if __name__ == '__main__':
    main()
