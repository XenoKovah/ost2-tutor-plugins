"""Tests for the Speed-runner! selection rules (scripts/speed_runner.py)."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import ignominious as ig  # noqa: E402
import speed_runner as sr  # noqa: E402

T = dt.datetime(2025, 1, 1, 12, 0, 0)
GAP, RUN_MIN = 30, 10
RUN, OTHER_RUN = 'course-v1:O+Arch1001+2021', 'course-v1:O+Arch1001+2024'
CLASSES = [{'title': 'Arch1001', 'filters': json.dumps({'course': [RUN, OTHER_RUN]})}]
RUN_CLASS = ig.class_of_run(CLASSES)


def clicks(n, every=5, start=T):
    return [start + dt.timedelta(seconds=every * i) for i in range(n)]


def at(minutes):
    return T + dt.timedelta(minutes=minutes)


def test_ten_quick_clicks_are_a_rush_and_nine_are_not():
    assert sr.rush_profile(clicks(10), GAP, RUN_MIN)[:2] == (10, 10)
    assert sr.rush_profile(clicks(9), GAP, RUN_MIN)[:2] == (0, 9)


def test_a_slow_click_breaks_the_run():
    times = clicks(6) + clicks(6, start=T + dt.timedelta(minutes=10))
    assert sr.rush_profile(times, GAP, RUN_MIN)[:2] == (0, 6)


def test_unengaged_rush_is_confirmed():
    v = sr.judge(clicks(12), {}, {}, content=40, allowlisted=True, gap=GAP, run_min=RUN_MIN)
    assert (v['status'], v['rushed'], v['longest']) == ('CONFIRMED', 12, 12)


def test_watching_a_quarter_of_the_content_before_the_burst_exonerates():
    burst = clicks(12, start=at(60))
    watched = {'v%d' % i: at(i) for i in range(10)}
    assert sr.judge(burst, watched, {}, content=40, allowlisted=True, gap=GAP, run_min=RUN_MIN)['status'] == 'EXONERATED'


def test_watching_after_the_burst_does_not_exonerate():
    burst = clicks(12)
    watched = {'v%d' % i: at(60 + i) for i in range(40)}
    assert sr.judge(burst, watched, {}, content=40, allowlisted=True, gap=GAP, run_min=RUN_MIN)['status'] == 'CONFIRMED'


def test_a_course_outside_the_allowlist_is_never_confirmed():
    assert sr.judge(clicks(12), {}, {}, content=40, allowlisted=False, gap=GAP, run_min=RUN_MIN)['status'] == 'UNCHECKABLE'


def test_course_team_staff_inactive_and_beta_completers_are_left_out():
    confirmed = {'status': 'CONFIRMED', 'start': T, 'longest': 12, 'rushed': 12}
    verdicts = {('1', RUN): confirmed, ('2', RUN): confirmed, ('3', OTHER_RUN): confirmed,
                ('4', RUN): dict(confirmed, status='EXONERATED')}
    kept, dropped = sr.select(verdicts, RUN_CLASS, excluded={'1'}, exempt={('2', 'Arch1001')})
    assert list(kept) == [('3', OTHER_RUN)]
    assert sum(dropped.values()) == 2


def test_beta_pairs_match_case_insensitively_by_run_suffix():
    rows = [{'p_username': 'Alice', 'p_run': 'Arch1001+2021'}, {'p_username': 'bob', 'p_run': 'Nope+2021'}]
    pairs, unmatched = sr.decided_pairs(rows, {'alice': '7', 'bob': '8'}, RUN_CLASS)
    assert pairs == {('7', 'Arch1001')}
    assert unmatched == [('bob', 'Nope+2021')]


def test_only_gold_is_demoted_once_per_class_on_the_certified_run():
    badges = {'Arch1001': {'gold': (11, 10000), 'silver': (12, 5000), 'bronze': (13, 2500), 'plain': (14, 1000)}}
    v = {'status': 'CONFIRMED', 'start': T, 'longest': 12, 'rushed': 30}
    kept = {('1', RUN): v, ('1', OTHER_RUN): dict(v, start=at(5)), ('2', RUN): v}
    certs = {'1': [{'course_id': OTHER_RUN, 'created': '2025-03-01 00:00:00'}, {'course_id': RUN, 'created': '2025-02-01 00:00:00'}]}
    held = {('alice', 'Arch1001'): 'gold', ('bob', 'Arch1001'): 'silver'}
    rows = sr.demotions(kept, RUN_CLASS, {'1': 'alice', '2': 'bob'}, held, badges, certs, {'Arch1001': {RUN, OTHER_RUN}})
    assert [(r['username'], r['gold_badge_id'], r['plain_badge_id'], r['cert_run'], r['course_id']) for r in rows] == [
        ('alice', 11, 14, RUN, RUN)]


def test_tier_comes_from_the_category():
    assert sr.tier_of('Course Completion / Gold') == 'gold'
    assert sr.tier_of('Course Completion / Plain') == 'plain'
