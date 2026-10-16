"""Tests for the Multi-class Accomplishments selection rules (scripts/multi_class.py)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import multi_class as mc  # noqa: E402

A, A2, B, C = 'course-v1:O+A+1', 'course-v1:O+A+2', 'course-v1:O+B+1', 'course-v1:O+C+1'


def rule_row(badge, rule, courses, points=10000, event='edx_certificate_created', action=None, filters=None, excluded='[]'):
    return {'badge_id': badge, 'title': 'Badge %s' % badge, 'points': str(points), 'excluded_categories': excluded,
            'rule_id': rule, 'event': event, 'action': json.dumps({event: action or {'count': 1}}),
            'filters': json.dumps(filters if filters is not None else {'course': courses})}


def cert(user, course, active='1'):
    return {'username': user, 'course_id': course, 'is_active': active}


def test_every_rule_must_be_met_and_any_run_meets_a_rule():
    badges, problems = mc.load_badges([rule_row('1', '10', [A, A2]), rule_row('1', '11', B)])
    assert problems == []
    rules = badges['1']['rules']
    assert mc.qualifies({A2, B}, rules)
    assert not mc.qualifies({A, A2}, rules)


def test_one_certificate_can_meet_several_rules():
    badges, _ = mc.load_badges([rule_row('1', '10', [A, B]), rule_row('1', '11', [A, C])])
    assert mc.qualifies({A}, badges['1']['rules'])


def test_course_ids_match_exactly_and_case_only_matches_are_reported():
    badges, _ = mc.load_badges([rule_row('1', '10', 'course-v1:O+RE+2021_V1')])
    certs = [cert('u', 'course-v1:O+RE+2021_v1')]
    courses, _ = mc.learner_courses(certs)
    assert not mc.qualifies(courses['u'], badges['1']['rules'])
    assert mc.case_only_matches(certs, {'course-v1:O+RE+2021_V1'}) == [('u', 'course-v1:O+RE+2021_v1', 'course-v1:O+RE+2021_V1')]


def test_anything_but_a_plain_certificate_rule_aborts():
    rows = [rule_row('1', '10', A, filters={'course': A, 'completion_window': {'max_weeks': 2}}),
            rule_row('2', '20', A, action={'count': 2}),
            rule_row('3', '30', A, event='stop_video'),
            rule_row('4', '40', A, excluded='["Ignominious!"]')]
    _, problems = mc.load_badges(rows)
    assert len(problems) == 4


def test_retired_and_inactive_accounts_are_skipped():
    courses, skipped = mc.learner_courses([cert('ok', A), cert('retired__user_abc', A), cert('gone', A, active='0')])
    assert set(courses) == {'ok'}
    assert skipped == {'retired__user_abc': 'retired', 'gone': 'inactive'}


def test_plan_skips_earned_and_split_reuses_in_progress_and_orders_by_level():
    badges, _ = mc.load_badges([rule_row('1', '10', A, points=20000), rule_row('2', '20', A, points=10000),
                                rule_row('3', '30', A), rule_row('4', '40', A)])
    state = mc.existing_state([
        {'user_uid': 'u', 'badge_id': '3', 'completed': '1', 'rules': '1', 'rules_completed': '1'},
        {'user_uid': 'u', 'badge_id': '4', 'completed': '1', 'rules': '1', 'rules_completed': '0'},
        {'user_uid': 'u', 'badge_id': '2', 'completed': '0', 'rules': '1', 'rules_completed': '0'},
        {'user_uid': 'v', 'badge_id': '1', 'completed': '1', 'rules': '0', 'rules_completed': '0'},
    ])
    grants, counts, not_qualifying, split = mc.plan({'u': {A: '2025-01-01 00:00:00'}}, badges, state)
    assert [(g['username'], g['badge_id']) for g in grants] == [('u', '2'), ('u', '1')]
    assert counts['3']['already earned'] == 1 and counts['4']['split state'] == 1
    assert split == [('u', '4')]
    assert not_qualifying == [('v', '1')]


def test_cross_check_flags_classes_without_exactly_one_tier():
    badges, _ = mc.load_badges([rule_row('1', '10', [A, A2]), rule_row('1', '11', [B, C])])
    grants = [{'username': 'u', 'badge_id': '1', 'title': 'Badge 1', 'points': 10000}]
    run_class = {A: 'Class A', A2: 'Class A', B: 'Class B'}
    held = [{'user_uid': 'u', 'title': 'Class A'}]
    bad, no_cc = mc.cross_check(grants, {'u': dict.fromkeys((A, B, C), '2025-01-01 00:00:00')}, badges, run_class, held)
    assert bad == [('u', 'Class B', 0)]
    assert no_cc == [C]


def test_a_badge_is_dated_when_its_last_rule_was_first_met():
    """Back-dated to the moment the live engine would have granted it (decided 2026-10-02)."""
    badges, _ = mc.load_badges([rule_row('1', '10', [A, A2]), rule_row('1', '11', B)])
    certs = [dict(cert('u', A), created='2021-05-01 10:00:00.000000'), dict(cert('u', A2), created='2020-03-01 09:00:00'),
             dict(cert('u', B), created='2023-07-04 12:30:00.123456')]
    courses, _ = mc.learner_courses(certs)
    assert mc.earned_at(courses['u'], badges['1']['rules']) == '2023-07-04 12:30:00.123456'
    grants, _, _, _ = mc.plan(courses, badges, {})
    assert grants[0]['completed_at'] == '2023-07-04 12:30:00'

