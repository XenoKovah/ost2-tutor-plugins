"""
Apply one shard of the Multi-class Accomplishments plan written by multi_class.py (RGG/backfills).

Runs in the rgg container through `manage.py shell` (see run_multi_class_backfill.sh), with
SHARD_K / SHARD_N / APPLY=1 (and RGG_SKIP_POINTS_REFRESH=true) in the environment. Per learner, in one
transaction under the learner's row lock (the lock the live event signal takes), their badges in level
order:
  * already earned (completed with every rule completed, or rule-less) -> skipped;
  * completed with rules not all completed (split state)              -> skipped and reported;
  * otherwise -> the engine's own rows: the Achievement (reusing an in-progress one), every rule synced
    as completed by AchievementReconciliationService._sync_rules, then AchievementCompletionUseCase,
    dated when the badge's last rule was first met (the plan's completed_at, decided 2026-10-02), pays
    Badge.points once, records completion_points_paid and emits the internal achievement event.
    Silent: notification_seen_at = completed_at.
Multi-class badges are not course-scoped: no course board changes. The learner's leaderboards are
queued by the points save itself.
"""
import collections
import csv
import os
import zlib
from datetime import datetime, timezone

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from achievements.models import Achievement, AchievementRule
from achievements.reconciliation import AchievementReconciliationService
from achievements.usecases import AchievementCompletionUseCase
from badges.models import Badge
from users.models import GammaUser

assert os.environ.get('APPLY') == '1', 'APPLY=1 is required'
K, N = int(os.environ['SHARD_K']), int(os.environ['SHARD_N'])
MARK = 'multi_class_backfill'
badge_type = ContentType.objects.get_for_model(Badge)
service = AchievementReconciliationService()

rows = [r for r in csv.DictReader(open('/tmp/mcapply/plan.tsv'), delimiter='\t')
        if zlib.crc32(r['username'].encode()) % N == K]
by_user = collections.OrderedDict()
for r in rows:
    by_user.setdefault(r['username'], []).append(r)
badges = {b.id: b for b in Badge.objects.filter(id__in={int(r['badge_id']) for r in rows}).prefetch_related('rules')}
out, points, created_users = collections.Counter(), 0, 0
for username, user_rows in by_user.items():
    with transaction.atomic():
        existed = GammaUser.objects.filter(user_uid=username).exists()
        user = GammaUser.ensure_gamma_user_is_created(user_uid=username)
        created_users += not existed
        user = GammaUser.objects.select_for_update().get(pk=user.pk)
        for r in sorted(user_rows, key=lambda r: (int(r['points']), int(r['badge_id']))):
            badge = badges[int(r['badge_id'])]
            rules = list(badge.rules.all())
            achievement = Achievement.objects.filter(user=user, content_type=badge_type, object_id=badge.id).first()
            if achievement is not None and achievement.completed_at is not None:
                if not achievement.achievement_rules.exists() or achievement.all_rules_completed:
                    out['already earned'] += 1
                else:
                    out['split state'] += 1
                    print('SPLIT %s %s' % (username, badge.id))
                continue
            if achievement is None:
                achievement = Achievement.objects.create(
                    user=user, content_type=badge_type, object_id=badge.id,
                    title=(badge.title or '')[:64], description=badge.description,
                )
            service._sync_rules(achievement, rules, {rule.id: True for rule in rules}, user)  # pylint: disable=protected-access
            for link in AchievementRule.objects.filter(achievement=achievement):
                link.dependencies = dict(link.dependencies or {}, backfill=MARK)
                link.save(update_fields=('dependencies',))
            achievement.user = user
            earned = datetime.strptime(r['completed_at'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            AchievementCompletionUseCase().execute(achievement, completed_at=earned)
            achievement.refresh_from_db(fields=('completed_at', 'completion_points_paid', 'notification_seen_at'))
            if achievement.completed_at is None or not achievement.all_rules_completed:
                raise RuntimeError('grant of badge %s to %s did not complete' % (badge.id, username))
            Achievement.objects.filter(pk=achievement.pk, notification_seen_at__isnull=True).update(
                notification_seen_at=achievement.completed_at)
            paid = achievement.completion_points_paid or 0
            points += paid
            out['granted'] += 1
            print('GRANTED|%s|%d|%d' % (username, badge.id, paid))
print('SHARD %d/%d learners %d rows %d: %s; points %d; GammaUsers created %d' % (
    K, N, len(by_user), len(rows), ', '.join('%s=%d' % kv for kv in sorted(out.items())), points, created_users))
