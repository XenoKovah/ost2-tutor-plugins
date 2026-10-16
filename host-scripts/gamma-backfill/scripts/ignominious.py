#!/usr/bin/env python3
"""
Who gets the manual "Ignominious!" badges Incompletionist and Watchlister, computed from LMS data.

Classes are the 33 Course Completion classes, each the set of runs its tier rules list, so a
learner's runs of one class count once. Per learner and class:
  * enrolled  = an ACTIVE enrollment ("Enrolled", not Unenrolled) in a run of the class; the
                earliest active enrollment dates it. Unenrolled learners are not penalized.
  * complete  = a downloadable certificate in any run of the class.
  * started   = any "Mark as complete" click (done:true), or a grade above 0, in any run.
Incompletionist: enrolled in a class over `min_age_days` (365) ago, started it and not complete
  (decided 2026-10-01: only classes they started, as the badge text says; 10,212 on dev).
Watchlister: enrolled in at least `min_classes` (5) classes they have not started and not
  completed, with no minimum enrollment age (decided 2026-10-01; 1,059 on dev).
Learners on a course team (staff/instructor on any course), site staff/superusers and
inactive accounts are left out, as decided for Beta Flake.
Usage: ignominious.py EXTRACT_DIR OUTDIR [--incompletionist-include-unstarted] [--watchlister-min-age-days N]
"""
import argparse
import collections
import csv
import datetime as dt
import json
import os


def read(path):
    if not os.path.getsize(path):
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE))


def ts(value):
    return dt.datetime.strptime(value[:19], '%Y-%m-%d %H:%M:%S')


def class_of_run(class_rows):
    """{course run: class title} from the tier rules' course filters."""
    runs = {}
    for r in class_rows:
        course = json.loads(r['filters'])['course']
        for run in (course if isinstance(course, list) else [course]):
            assert runs.get(run, r['title']) == r['title'], ('run in two classes', run)
            runs[run] = r['title']
    return runs


def learner_classes(enrollments, certificates, clicks, grades, run_class):
    """{(user_id, class): {'enrolled_at', 'complete', 'started'}} for active enrollments only."""
    done = {(r['user_id'], run_class[r['course_id']]) for r in certificates if r['course_id'] in run_class}
    started = {(r['user_id'], run_class[r['course_id']]) for r in clicks if r['course_id'] in run_class}
    started |= {(r['user_id'], run_class[r['course_id']]) for r in grades if r['course_id'] in run_class}
    out = {}
    for e in enrollments:
        if e['is_active'] != '1' or e['course_id'] not in run_class:
            continue
        key = (e['user_id'], run_class[e['course_id']])
        at = ts(e['created'])
        if key not in out or at < out[key]['enrolled_at']:
            out[key] = {'enrolled_at': at, 'complete': key in done, 'started': key in started}
    return out


def select_incompletionists(lc, now, excluded, min_age_days=365, require_started=True):
    """{user_id: [classes]}: enrolled over min_age_days ago, not complete (and started, if required)."""
    cutoff = now - dt.timedelta(days=min_age_days)
    found = collections.defaultdict(list)
    for (user, cls), v in lc.items():
        if user in excluded or v['complete'] or v['enrolled_at'] >= cutoff:
            continue
        if require_started and not v['started']:
            continue
        found[user].append(cls)
    return {u: sorted(c) for u, c in found.items()}


def select_watchlisters(lc, now, excluded, min_classes=5, min_age_days=0):
    """{user_id: [classes]}: at least min_classes classes enrolled, never started, not complete."""
    cutoff = now - dt.timedelta(days=min_age_days)
    found = collections.defaultdict(list)
    for (user, cls), v in lc.items():
        if user in excluded or v['complete'] or v['started'] or v['enrolled_at'] >= cutoff:
            continue
        found[user].append(cls)
    return {u: sorted(c) for u, c in found.items() if len(c) >= min_classes}


def excluded_users(users, course_team):
    """Course-team members, site staff/superusers and inactive accounts."""
    team = {r['user_id'] for r in course_team}
    return {u['id'] for u in users if u['id'] in team or u['is_staff'] == '1' or u['is_superuser'] == '1' or u['is_active'] != '1'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('extract')
    ap.add_argument('out')
    ap.add_argument('--incompletionist-include-unstarted', action='store_true',
                    help='also count classes never started (the broader reading; not the decided one)')
    ap.add_argument('--watchlister-min-age-days', type=int, default=0)
    a = ap.parse_args()
    X = lambda n: read(os.path.join(a.extract, n))
    enrollments = X('enrollments.tsv')
    now = ts(enrollments[0]['q_now_utc'])
    run_class = class_of_run(X('classes.tsv'))
    users = {u['id']: u for u in X('users.tsv')}
    excluded = excluded_users(users.values(), X('course_team.tsv'))
    lc = learner_classes(enrollments, X('certificates.tsv'), X('clicks.tsv'), X('grades.tsv'), run_class)
    inc = select_incompletionists(lc, now, excluded, require_started=not a.incompletionist_include_unstarted)
    wat = select_watchlisters(lc, now, excluded, min_age_days=a.watchlister_min_age_days)
    os.makedirs(a.out, exist_ok=True)
    for name, chosen in (('incompletionist', inc), ('watchlister', wat)):
        with open(os.path.join(a.out, name + '.txt'), 'w') as f:
            f.write(''.join(users[u]['username'] + '\n' for u in sorted(chosen, key=lambda u: users[u]['username'].lower())))
        with open(os.path.join(a.out, name + '-detail.tsv'), 'w') as f:
            f.write('username\tclasses\n')
            for u in sorted(chosen, key=lambda u: users[u]['username'].lower()):
                f.write('%s\t%s\n' % (users[u]['username'], ' | '.join(chosen[u])))
    print('as of %s UTC: %d classes, %d active learner-classes, %d learners excluded (course team, staff, inactive)' % (
        now, len(set(run_class.values())), len(lc), len(excluded & {u for u, c in lc})))
    print('Incompletionist%s: %d learners' % ('' if a.incompletionist_include_unstarted else ' (started classes only)', len(inc)))
    print('Watchlister%s: %d learners' % (' (enrollments older than %d days)' % a.watchlister_min_age_days if a.watchlister_min_age_days else '', len(wat)))


if __name__ == '__main__':
    main()
