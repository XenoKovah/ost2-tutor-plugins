"""
Test for scripts/mc_apply.py, run inside a gamma checkout (6188bc9 or later) the same way as
test_points_refresh_shard.py (copy into <gamma>/zz_mc/, pytest with gamma.settings.test, then remove).
"""
import io, os, contextlib
import pytest
from django.contrib.contenttypes.models import ContentType
from achievements.models import Achievement
from badges.models import Badge
from users.models import GammaUser

pytestmark = pytest.mark.django_db
CERT = 'edx_certificate_created'
SCRIPT = '/home/user/Downloads/RGG/backfills/scripts/mc_apply.py'


def run(monkeypatch):
    monkeypatch.setenv('APPLY', '1'); monkeypatch.setenv('SHARD_K', '0'); monkeypatch.setenv('SHARD_N', '1')
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(open(SCRIPT).read(), SCRIPT, 'exec'), {'__name__': '__main__'})
    return out.getvalue()


def test_grant_path(monkeypatch, event_configuration_factory, rule_factory, badge_factory, gamma_user_factory, settings):
    settings.RGG_SKIP_POINTS_REFRESH = True
    conf = event_configuration_factory(event_type__name=CERT)
    r1 = rule_factory(event_configuration=conf, action={CERT: {'count': 1}}, filters={'course': ['c:A+1', 'c:A+2']})
    r2 = rule_factory(event_configuration=conf, action={CERT: {'count': 1}}, filters={'course': 'c:B+1'})
    low = badge_factory(points=10000)
    low.rules.set([r1, r2])
    gamma_user_factory(user_uid='alice', points=5)
    ct = ContentType.objects.get_for_model(Badge)
    bob = gamma_user_factory(user_uid='bob', points=0)
    Achievement.objects.create(user=bob, content_type=ct, object_id=low.id, title='x', description='')
    os.makedirs('/tmp/mcapply', exist_ok=True)
    with open('/tmp/mcapply/plan.tsv', 'w') as f:
        f.write('username\tbadge_id\ttitle\tpoints\n')
        for u in ('alice', 'bob', 'carol'):
            f.write('%s\t%d\t%s\t10000\n' % (u, low.id, low.title))
    out = run(monkeypatch)
    print(out)
    assert 'granted=3' in out and 'GammaUsers created 1' in out
    for u, before in (('alice', 5), ('bob', 0), ('carol', 0)):
        user = GammaUser.objects.get(user_uid=u)
        a = Achievement.objects.get(user=user, content_type=ct, object_id=low.id)
        assert user.points == before + 10000
        assert a.completed_at and a.all_rules_completed and a.completion_points_paid == 10000
        assert a.notification_seen_at == a.completed_at
        deps = [l.dependencies for l in a.achievement_rules.all()]
        assert len(deps) == 2 and all(d['backfill'] == 'multi_class_backfill' and d['is_achieved'] for d in deps)
        assert all(list(d['events'].values())[0]['count'] == 1 for d in deps)
    assert Achievement.objects.filter(content_type=ct, object_id=low.id).count() == 3
    again = run(monkeypatch)
    print(again)
    assert 'already earned=3' in again
    assert GammaUser.objects.get(user_uid='alice').points == 10005
