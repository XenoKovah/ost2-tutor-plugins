"""
dev only, one-off (2026-10-02): re-date the Multi-class Accomplishments grants that the first dev run
(13:30 UTC) dated at grant time, to when each badge's last rule was first met, as run_multi_class_backfill.sh
now does on its own. Touches only achievements whose rule links carry the backfill's marker.
Runs in the rgg container: manage.py shell < mc_redate.py, with /tmp/mc-qualified.tsv (multi_class.py's
qualified.tsv) copied in.
"""
import collections
import csv
from datetime import datetime, timezone

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from achievements.models import Achievement
from badges.models import Badge

badge_ct = ContentType.objects.get_for_model(Badge)
out = collections.Counter()
for r in csv.DictReader(open('/tmp/mc-qualified.tsv'), delimiter='\t'):
    if r['state'] != 'earned':
        out['not earned: %s' % r['state']] += 1
        continue
    when = datetime.strptime(r['completed_at'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    with transaction.atomic():
        a = Achievement.objects.select_for_update().filter(
            user__user_uid=r['username'], content_type=badge_ct, object_id=int(r['badge_id'])).first()
        if a is None or not any((link.dependencies or {}).get('backfill') == 'multi_class_backfill' for link in a.achievement_rules.all()):
            out['not a backfill grant'] += 1
            continue
        if a.completed_at == when:
            out['already dated'] += 1
            continue
        Achievement.objects.filter(pk=a.pk).update(completed_at=when, notification_seen_at=when)
        out['re-dated'] += 1
print('REDATE', dict(out))
