"""Tests for the Incompletionist / Watchlister selection rules (scripts/ignominious.py)."""
import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import ignominious as ig  # noqa: E402

NOW = dt.datetime(2026, 10, 1, 12, 0, 0)
OLD = (NOW - dt.timedelta(days=400)).strftime('%Y-%m-%d %H:%M:%S')
RECENT = (NOW - dt.timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
CLASSES = [
    {'title': 'Debuggers 1011', 'filters': json.dumps({'course': ['c:Dbg1011+2021', 'c:Dbg1011+2024']})},
    {'title': 'Arch1001', 'filters': json.dumps({'course': 'c:Arch1001+2021'})},
] + [{'title': 'Class %d' % n, 'filters': json.dumps({'course': 'c:K%d' % n})} for n in range(1, 7)]
RUNS = ig.class_of_run(CLASSES)


def enroll(user, course, created=OLD, active='1'):
    return {'user_id': user, 'course_id': course, 'created': created, 'is_active': active}


def classes(enrollments, certificates=(), clicks=(), grades=()):
    return ig.learner_classes(enrollments, list(certificates), list(clicks), list(grades), RUNS)


def test_runs_of_a_class_map_to_that_class():
    assert RUNS['c:Dbg1011+2021'] == RUNS['c:Dbg1011+2024'] == 'Debuggers 1011'
    assert RUNS['c:Arch1001+2021'] == 'Arch1001'


def test_a_run_in_two_classes_is_refused():
    with pytest.raises(AssertionError):
        ig.class_of_run(CLASSES + [{'title': 'Other', 'filters': json.dumps({'course': 'c:Arch1001+2021'})}])


def test_old_started_unfinished_enrollment_is_an_incompletionist():
    lc = classes([enroll('1', 'c:Arch1001+2021')], clicks=[{'user_id': '1', 'course_id': 'c:Arch1001+2021'}])
    assert ig.select_incompletionists(lc, NOW, set()) == {'1': ['Arch1001']}


@pytest.mark.parametrize(
    'enrollments, certificates',
    [
        ([enroll('1', 'c:Arch1001+2021', created=RECENT)], []),
        ([enroll('1', 'c:Arch1001+2021', active='0')], []),
        ([enroll('1', 'c:Dbg1011+2021')], [{'user_id': '1', 'course_id': 'c:Dbg1011+2024'}]),
        ([enroll('1', 'c:Unknown+2021')], []),
    ],
    ids=['enrolled under a year ago', 'unenrolled', 'certified in the other run', 'not a Course Completion class'],
)
def test_not_an_incompletionist(enrollments, certificates):
    clicks = [{'user_id': '1', 'course_id': e['course_id']} for e in enrollments]
    assert ig.select_incompletionists(classes(enrollments, certificates, clicks), NOW, set()) == {}


def test_the_earliest_active_enrollment_dates_the_class():
    lc = classes([enroll('1', 'c:Dbg1011+2021', created=OLD), enroll('1', 'c:Dbg1011+2024', created=RECENT)])
    assert lc[('1', 'Debuggers 1011')]['enrolled_at'] == ig.ts(OLD)


def test_incompletionist_counts_only_started_classes_by_default():
    """Decided 2026-10-01: never-started classes are Watchlister territory, not Incompletionist."""
    lc = classes(
        [enroll('1', 'c:Arch1001+2021'), enroll('1', 'c:K1')],
        clicks=[{'user_id': '1', 'course_id': 'c:Arch1001+2021'}],
    )
    assert ig.select_incompletionists(lc, NOW, set()) == {'1': ['Arch1001']}
    assert ig.select_incompletionists(lc, NOW, set(), require_started=False) == {'1': ['Arch1001', 'Class 1']}


def test_excluded_learners_get_neither_badge():
    lc = classes([enroll('1', 'c:K%d' % n) for n in range(1, 6)] + [enroll('1', 'c:Arch1001+2021')],
                 clicks=[{'user_id': '1', 'course_id': 'c:Arch1001+2021'}])
    assert ig.select_incompletionists(lc, NOW, {'1'}) == {}
    assert ig.select_watchlisters(lc, NOW, {'1'}) == {}


def test_five_unstarted_classes_make_a_watchlister():
    lc = classes([enroll('1', 'c:K%d' % n) for n in range(1, 6)] + [enroll('2', 'c:K%d' % n) for n in range(1, 5)])
    assert ig.select_watchlisters(lc, NOW, set()) == {'1': ['Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5']}


def test_started_completed_and_repeated_runs_do_not_count_toward_watchlister():
    lc = classes(
        [enroll('1', 'c:K%d' % n) for n in range(1, 4)]
        + [enroll('1', 'c:Dbg1011+2021'), enroll('1', 'c:Dbg1011+2024'), enroll('1', 'c:Arch1001+2021'), enroll('1', 'c:K4')],
        certificates=[{'user_id': '1', 'course_id': 'c:Arch1001+2021'}],
        grades=[{'user_id': '1', 'course_id': 'c:K4', 'percent_grade': '0.02'}],
    )
    # K1-K3 and Debuggers 1011 (two runs, one class) = 4 unstarted classes: not enough.
    assert ig.select_watchlisters(lc, NOW, set()) == {}


def test_watchlister_can_ignore_recent_enrollments():
    lc = classes([enroll('1', 'c:K%d' % n, created=RECENT) for n in range(1, 6)])
    assert '1' in ig.select_watchlisters(lc, NOW, set())
    assert ig.select_watchlisters(lc, NOW, set(), min_age_days=90) == {}


def test_excluded_users_are_course_team_staff_and_inactive_accounts():
    users = [
        {'id': '1', 'is_active': '1', 'is_staff': '0', 'is_superuser': '0'},
        {'id': '2', 'is_active': '1', 'is_staff': '0', 'is_superuser': '0'},
        {'id': '3', 'is_active': '1', 'is_staff': '1', 'is_superuser': '0'},
        {'id': '4', 'is_active': '0', 'is_staff': '0', 'is_superuser': '0'},
    ]
    assert ig.excluded_users(users, [{'user_id': '2'}]) == {'2', '3', '4'}
