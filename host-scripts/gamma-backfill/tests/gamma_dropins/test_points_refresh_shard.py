"""
Test for scripts/points_refresh_shard.py. Runs inside a gamma checkout (points-rings-refresh or later):

    cp scripts/points_refresh_shard.py <gamma>/badges/management/commands/backfill_points_refresh_shard.py
    mkdir <gamma>/zz_dropin && touch <gamma>/zz_dropin/__init__.py && cp tests/gamma_dropins/test_points_refresh_shard.py <gamma>/zz_dropin/
    cd <gamma> && DJANGO_SETTINGS_MODULE=gamma.settings.test pytest zz_dropin --no-cov   (needs a Redis; then remove both copies)
"""
import io

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command

from achievements.models import Achievement
from badges.models import Badge
from events.models import Event, EventConfiguration

pytestmark = pytest.mark.django_db
PD = 'rgg_points_distribution'


def run(*args):
    out = io.StringIO()
    call_command('backfill_points_refresh_shard', *args, stdout=out)
    return out.getvalue()


def ring(user, badge):
    a = Achievement.objects.filter(user=user, content_type=ContentType.objects.get_for_model(Badge), object_id=badge.id).first()
    if a is None:
        return None
    p = a.achievement_rules.get().dependencies['events'][PD]
    return p['count'], p['goal'], a.completed_at is not None, a.notification_seen_at is not None


def test_shards_dry_run_rerun(rule_factory, badge_factory, gamma_user_factory, achievement_factory, achievement_rule_factory):
    conf = EventConfiguration.objects.get(event_type__name=PD)
    low = badge_factory(set_rules=rule_factory(event_configuration=conf, action={PD: {'points': 2000}}, filters={}), points=0)
    high = badge_factory(set_rules=rule_factory(event_configuration=conf, action={PD: {'points': 20000}}, filters={}), points=0)
    badge_factory(set_rules=rule_factory(event_configuration=conf, action={PD: {'points': 5}}, filters={}), points=0, is_active=False)
    rich = gamma_user_factory(points=2500)
    negative = gamma_user_factory(points=-500)
    ct = ContentType.objects.get_for_model(Badge)
    for b in (low, high):
        a = achievement_factory(user=negative, content_type=ct, object_id=b.id, completed_at=None)
        achievement_rule_factory(achievement=a, rule=b.rules.get(), dependencies={'events': {PD: {'goal': b.rules.get().action[PD]['points'], 'count': -500}}, 'is_achieved': False})

    out = run('--dry-run')
    assert 'DRY 2 learners (0 already current), +1 granted' in out, out
    assert Event.objects.filter(configuration=conf).count() == 0
    assert ring(rich, low) is None

    out0, out1 = run('--shard', '0/2'), run('--shard', '1/2')
    assert 'learners' in out0 and 'learners' in out1
    assert ring(rich, low) == (2500, 2000, True, True)
    assert ring(rich, high) == (2500, 20000, False, False)
    assert ring(negative, low) == (0, 2000, False, False)
    assert ring(negative, high) == (0, 20000, False, False)
    assert Event.objects.filter(configuration=conf).count() == 2

    again = run()
    assert '2 learners (2 already current), +0 granted' in again, again
    assert Event.objects.filter(configuration=conf).count() == 2
