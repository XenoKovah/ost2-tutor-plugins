"""
Apply one shard of the Course Completion tier plan written by cc_regrade.py (RGG/backfills).

Runs in the rgg container through `manage.py shell` (see run_course_completion_regrade.sh), with
SHARD_K / SHARD_N / APPLY=1 in the environment. Per learner-class, under the learner's row lock:
  * holds the target tier        -> nothing (idempotent);
  * holds another tier           -> re-filed in place (badge, title, rule link; completed_at and
                                    notification_seen_at kept), points topped up, never clawed back;
  * holds none                   -> granted through the normal completion path, dated at the
                                    certificate: pays Badge.points once (completion_points_paid),
                                    emits the internal achievement event, and is silent (no toast);
  * holds several tiers          -> left alone and reported (COURSE_COMPLETION_TIER_TIMING.md 6.3).
Points added are also credited to the per-course board of the run the learner certified in, which
gamma's award path does not do (spec 5.2, 7), and the learner's leaderboards are queued.
"""
import collections
import csv
import os
import zlib
from datetime import datetime, timezone

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils.timezone import now

from achievements.models import Achievement, AchievementRule
from achievements.usecases import AchievementCompletionUseCase
from badges.models import Badge
from leaderboard.repository import RedisLeaderboardsPendingUpdateRepository
from users.models import GammaUser, GammaUserCoursePoints

assert os.environ.get('APPLY') == '1', 'APPLY=1 is required'
K, N = int(os.environ['SHARD_K']), int(os.environ['SHARD_N'])
MARK = 'cc_tier_regrade'
CERT_EVENT = 'edx_certificate_created'
badge_type = ContentType.objects.get_for_model(Badge)
leaderboards = RedisLeaderboardsPendingUpdateRepository()

rows = [r for r in csv.DictReader(open('/tmp/ccapply/plan.tsv'), delimiter='\t')
        if zlib.crc32(r['username'].encode()) % N == K]
ids = {int(i) for r in rows for i in r['class_badge_ids'].split(',')}
badges = {b.id: b for b in Badge.objects.filter(id__in=ids).prefetch_related('rules')}
out, points, course_points = collections.Counter(), 0, 0
for r in rows:
    target = badges[int(r['badge_id'])]
    [rule] = list(target.rules.all())
    certified_at = datetime.strptime(r['cert_created'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    with transaction.atomic():
        user = GammaUser.ensure_gamma_user_is_created(user_uid=r['username'])
        user = GammaUser.objects.select_for_update().get(pk=user.pk)
        held = list(Achievement.objects.filter(
            user=user, content_type=badge_type, completed_at__isnull=False,
            object_id__in=[int(i) for i in r['class_badge_ids'].split(',')],
        ))
        if len(held) > 1:
            out['conflict'] += 1
            print('CONFLICT %s %s holds %d tiers' % (r['username'], r['class'], len(held)))
            continue
        if held and held[0].object_id == target.id:
            out['keep'] += 1
            continue
        if held:
            achievement = held[0]
            old = badges[achievement.object_id]
            paid = achievement.completion_points_paid if achievement.completion_points_paid is not None else old.points
            topup = max(0, target.points - paid)
            achievement.object_id, achievement.title, achievement.description = target.id, target.title, target.description
            achievement.completion_points_paid = paid + topup
            achievement.save(update_fields=('object_id', 'title', 'description', 'completion_points_paid'))
            AchievementRule.objects.filter(achievement=achievement).update(rule=rule)
            if topup:
                user.update_user_points(topup)
                user.update_user_progress(topup)
            out['move'] += 1
        else:
            achievement, _ = Achievement.objects.get_or_create(
                user=user, content_type=badge_type, object_id=target.id,
                defaults={'title': target.title, 'description': target.description},
            )
            AchievementRule.objects.update_or_create(
                achievement=achievement, rule=rule,
                defaults={
                    'status': AchievementRule.Statuses.COMPLETED,
                    'dependencies': {
                        'is_achieved': True,
                        'events': {CERT_EVENT: {'goal': 1, 'count': 1, 'achieved_at': certified_at.isoformat()}},
                        'backfill': MARK,
                    },
                },
            )
            achievement.user = user
            AchievementCompletionUseCase().execute(achievement, completed_at=certified_at)
            Achievement.objects.filter(pk=achievement.pk, notification_seen_at__isnull=True).update(notification_seen_at=now())
            topup = achievement.completion_points_paid or 0
            out['new'] += 1
        if topup:
            board, created = GammaUserCoursePoints.objects.get_or_create(
                gamma_user=user, course_id=r['cert_run'], defaults={'points': topup},
            )
            if not created:
                board.points += topup
                board.save(update_fields=('points',))
            course_points += topup
        points += topup
        leaderboards.schedule_user_leaderboards_update(user.user_uid)
print('SHARD %d/%d rows %d: %s; points %d; course points %d' % (
    K, N, len(rows), ', '.join('%s=%d' % kv for kv in sorted(out.items())), points, course_points))
