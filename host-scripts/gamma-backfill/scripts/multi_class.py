#!/usr/bin/env python3
"""
Multi-class Accomplishments backfill selection (MULTI_CLASS_ACCOMPLISHMENTS_BACKFILL.md, 2026-10-02).

A learner qualifies for a badge when every rule on it is met (AND). A rule (`edx_certificate_created`,
count 1, a `course` filter) is met by a downloadable LMS certificate in any course id of its filter (OR),
matched as an exact string, as the engine does. One certificate can meet several rules. Gamma's own
certificate events are not used: gamma is missing most historical certificates.
  * Skipped and reported: retired and inactive accounts.
  * Already earned, skipped: a completed achievement whose rules are all completed, or a rule-less one.
  * Reported, never changed: completed achievements with rules not all completed (split state), and
    holders who don't qualify. The backfill only grants.
  * Aborts if a rule isn't a plain certificate rule (another event, count != 1, a filter other than
    `course`), if a badge has excluded_categories, or if another rule depends on one of these badges.
Outputs (OUTDIR):
  plan.tsv   : username, badge_id, title, points, completed_at; the grants, by learner, then by points (level
               order). completed_at = when the badge's last rule was first met by a certificate (UTC).
  qualified.tsv : every qualifying (learner, badge) with that date and its current state (for audits).
  report.txt : per badge and totals, the anomalies, the predicted points delta and the site total the
               apply must start from.
Usage: multi_class.py EXTRACT_DIR OUTDIR
"""
import collections
import csv
import json
import os
import sys

CERT_EVENT = 'edx_certificate_created'


def read(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE))


def courses_of(filters):
    course = (filters or {}).get('course')
    return frozenset(course if isinstance(course, list) else [course] if course else [])


def load_badges(rows):
    """{badge_id: {title, points, rules: {rule_id: frozenset(course ids)}}} and the reasons to abort."""
    badges, problems = {}, []
    for r in rows:
        b = badges.setdefault(r['badge_id'], {'title': r['title'], 'points': int(r['points']), 'rules': {}})
        action = json.loads(r['action'] or '{}').get(r['event'] or '', {})
        filters = json.loads(r['filters'] or '{}')
        if r['event'] != CERT_EVENT or action.get('count') not in (1, '1') or set(filters) - {'course'}:
            problems.append('badge %s rule %s is not a plain certificate rule: event %s, action %s, filters %s' % (
                r['badge_id'], r['rule_id'], r['event'], action, sorted(filters)))
        if json.loads(r['excluded_categories'] or '[]'):
            problems.append('badge %s has excluded_categories %s' % (r['badge_id'], r['excluded_categories']))
        b['rules'][r['rule_id']] = courses_of(filters)
    for bid, b in badges.items():
        problems += ['badge %s rule %s has no course' % (bid, rid) for rid, c in b['rules'].items() if not c]
    return badges, sorted(set(problems))


def learner_courses(certs):
    """({username: {course id: earliest certificate time}} for active accounts, {username: reason} for the skipped ones)."""
    courses, skipped = collections.defaultdict(dict), {}
    for c in certs:
        if c['username'].startswith('retired__user_'):
            skipped[c['username']] = 'retired'
        elif c['is_active'] != '1':
            skipped[c['username']] = 'inactive'
        else:
            held = courses[c['username']]
            held[c['course_id']] = min(held.get(c['course_id'], c.get('created', '')), c.get('created', ''))
    return courses, skipped


def earned_at(held, rules):
    """When the badge was earned: the moment its last rule was first met (latest of each rule's first certificate)."""
    return max(min(held[c] for c in held.keys() & rule_courses) for rule_courses in rules.values())


def qualifies(courses, rules):
    return bool(rules) and all(set(courses) & rule_courses for rule_courses in rules.values())


def existing_state(rows):
    """{(username, badge_id): 'earned' | 'split' | 'in progress'}."""
    state = {}
    for r in rows:
        if r['completed'] != '1':
            state[(r['user_uid'], r['badge_id'])] = 'in progress'
        elif r['rules'] == '0' or r['rules'] == r['rules_completed']:
            state[(r['user_uid'], r['badge_id'])] = 'earned'
        else:
            state[(r['user_uid'], r['badge_id'])] = 'split'
    return state


def plan(courses, badges, state):
    """(grants, per-badge counts, holders who don't qualify, split-state rows)."""
    grants, counts = [], collections.defaultdict(collections.Counter)
    for user, held in sorted(courses.items()):
        for bid, b in sorted(badges.items(), key=lambda kv: (kv[1]['points'], int(kv[0]))):
            if not qualifies(held, b['rules']):
                continue
            counts[bid]['qualify'] += 1
            st = state.get((user, bid))
            if st in ('earned', 'split'):
                counts[bid]['already earned' if st == 'earned' else 'split state'] += 1
                continue
            counts[bid]['grant'] += 1
            grants.append({'username': user, 'badge_id': bid, 'title': b['title'], 'points': b['points'],
                           'completed_at': earned_at(held, b['rules'])[:19]})
    not_qualifying = sorted((u, bid) for (u, bid), st in state.items()
                            if st in ('earned', 'split') and bid in badges and not qualifies(courses.get(u, set()), badges[bid]['rules']))
    split = sorted(k for k, st in state.items() if st == 'split')
    return grants, counts, not_qualifying, split


def case_only_matches(certs, rule_courses):
    """Certificates whose course id matches a rule course only when case is ignored."""
    lower = {c.lower(): c for c in rule_courses}
    return sorted({(c['username'], c['course_id'], lower[c['course_id'].lower()]) for c in certs
                   if c['course_id'] not in rule_courses and c['course_id'].lower() in lower})


def class_of_run(rows):
    runs = {}
    for r in rows:
        for run in courses_of(json.loads(r['filters'])):
            runs[run] = r['title']
    return runs


def cross_check(grants, courses, badges, cc_run_class, cc_held):
    """Classes a granted learner used that they don't hold exactly one Course Completion tier for."""
    held = collections.Counter((r['user_uid'], r['title']) for r in cc_held)
    bad, no_cc_badge = set(), set()
    for g in grants:
        for rule_courses in badges[g['badge_id']]['rules'].values():
            for course in courses[g['username']].keys() & rule_courses:
                cls = cc_run_class.get(course)
                if cls is None:
                    no_cc_badge.add(course)
                elif held[(g['username'], cls)] != 1:
                    bad.add((g['username'], cls, held[(g['username'], cls)]))
    return sorted(bad), sorted(no_cc_badge)


def main():
    extract, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    X = lambda name: read(os.path.join(extract, name))
    badges, problems = load_badges(X('badges.tsv'))
    dependents = X('dependents.tsv')
    problems += ['rule %s depends on badge %s' % (d['rule_id'], d['dependent_object_id']) for d in dependents]
    if not badges:
        problems.append('no active Multi-class Accomplishments badge')
    certs = X('certificates.tsv')
    courses, skipped = learner_courses(certs)
    state = existing_state(X('achievements.tsv'))
    grants, counts, not_qualifying, split = plan(courses, badges, state)
    rule_courses = set().union(*(c for b in badges.values() for c in b['rules'].values())) if badges else set()
    cert_courses = {c['course_id'] for c in certs}
    overviews = {r['id'] for r in X('course_overviews.tsv')}
    gamma_users = {r['user_uid'] for r in X('gamma_users.tsv')}
    lms_pairs = {(c['username'], c['course_id']) for c in certs}
    synthetic = sorted({(e['user_uid'], e['course_id']) for e in X('cert_events.tsv')} - lms_pairs)
    skipped_qualifying = sorted(u for u, why in skipped.items()
                                if any(qualifies({c['course_id'] for c in certs if c['username'] == u}, b['rules']) for b in badges.values()))
    bad_cc, no_cc = cross_check(grants, courses, badges, class_of_run(X('cc_classes.tsv')), X('cc_tiers_held.tsv'))
    total = int(X('site_total.tsv')[0]['total'])

    with open(os.path.join(out, 'qualified.tsv'), 'w') as f:
        f.write('username\tbadge_id\tcompleted_at\tstate\n')
        for user, held in sorted(courses.items()):
            for bid, b in sorted(badges.items(), key=lambda kv: int(kv[0])):
                if qualifies(held, b['rules']):
                    f.write('%s\t%s\t%s\t%s\n' % (user, bid, earned_at(held, b['rules'])[:19], state.get((user, bid), 'to grant')))
    with open(os.path.join(out, 'plan.tsv'), 'w') as f:
        f.write('username\tbadge_id\ttitle\tpoints\tcompleted_at\n')
        for g in grants:
            f.write('%(username)s\t%(badge_id)s\t%(title)s\t%(points)d\t%(completed_at)s\n' % g)
    P = lambda n: format(n, ',')
    S = []
    w = S.append
    w('Multi-class Accomplishments: %d active badges; %s downloadable certificates (%s learners)' % (
        len(badges), P(len(certs)), P(len({c['username'] for c in certs}))))
    w('ABORT: ' + '; '.join(problems) if problems else 'rules: all plain certificate rules, no excluded_categories, no dependents')
    w('per badge: qualify | already earned | split state | to grant | points to pay')
    for bid, b in sorted(badges.items(), key=lambda kv: (kv[1]['title'])):
        c = counts[bid]
        w('  %-45s %5d | %5d | %3d | %5d | %s' % (b['title'][:45], c['qualify'], c['already earned'], c['split state'], c['grant'], P(c['grant'] * b['points'])))
    learners = {g['username'] for g in grants}
    w('grants %s to %s learners; points delta %s; GammaUsers to create %d' % (
        P(len(grants)), P(len(learners)), P(sum(g['points'] for g in grants)), len(learners - gamma_users)))
    w('SITE_TOTAL_BEFORE %d' % total)
    w('PREDICTED_DELTA %d' % sum(g['points'] for g in grants))
    w('anomalies:')
    w('  case-only course-id matches: %d %s' % (len(case_only_matches(certs, rule_courses)), case_only_matches(certs, rule_courses)[:5]))
    missing = sorted((c, c in cert_courses, c in overviews) for c in rule_courses if c not in cert_courses or c not in overviews)
    w('  rule courses with no certificate or no CourseOverview: %d %s' % (len(missing), ['%s (certs %s, overview %s)' % m for m in missing]))
    w('  retired/inactive accounts that would qualify (skipped): %d %s' % (len(skipped_qualifying), skipped_qualifying[:10]))
    w('  holders who do not qualify (left alone): %d %s' % (len(not_qualifying), not_qualifying[:10]))
    w('  split-state achievements (left alone): %d %s' % (len(split), split[:10]))
    w('  gamma certificate events with no LMS certificate (not counted): %d %s' % (len(synthetic), synthetic[:5]))
    w('cross-check with the Course Completion tiers: %d learner-classes without exactly one tier %s' % (len(bad_cc), bad_cc[:10]))
    w('  rule courses that have no Course Completion badge: %s' % (no_cc or 'none'))
    w('completed_at: the latest of each rule\'s first certificate (when the live engine would have granted it), decided 2026-10-02')
    open(os.path.join(out, 'report.txt'), 'w').write('\n'.join(S) + '\n')
    print('\n'.join(S))
    if problems:
        sys.exit(2)


if __name__ == '__main__':
    main()
