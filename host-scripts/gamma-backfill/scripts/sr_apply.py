"""
Apply one shard of the Speed-runner! Gold -> Plain demotions written by speed_runner.py (RGG/backfills).

Runs in the rgg container through `manage.py shell` (see run_speed_runner_backfill.sh), with
SHARD_K / SHARD_N / APPLY=1 in the environment. Per (learner, class), under the learner's row lock:
  * holds Plain for the class    -> nothing (idempotent);
  * holds Gold                   -> re-filed in place as Plain (badge, title, rule link; completed_at and
                                    notification_seen_at kept, the rule link records the demotion), and
                                    the Gold-Plain difference of what was paid is clawed back from the
                                    learner's points, their progress timeline (today) and the course board
                                    of the run they certified in (never below 0);
  * holds another tier, none, or several -> left alone and reported.
Points changes re-evaluate the learner's points badges after commit, and their leaderboards are queued.
The Speed-runner! badge itself is granted separately, with grant_manual_badge.sh.
"""
import collections
import csv
import os
import zlib

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from achievements.models import Achievement, AchievementRule
from badges.models import Badge
from leaderboard.repository import RedisLeaderboardsPendingUpdateRepository
from users.models import GammaUser, GammaUserCoursePoints

assert os.environ.get('APPLY') == '1', 'APPLY=1 is required'
K, N = int(os.environ['SHARD_K']), int(os.environ['SHARD_N'])
MARK = 'speed_runner_demoted_from'
badge_type = ContentType.objects.get_for_model(Badge)
leaderboards = RedisLeaderboardsPendingUpdateRepository()

rows = [r for r in csv.DictReader(open('/tmp/srapply/demotions.tsv'), delimiter='\t')
        if zlib.crc32(r['username'].encode()) % N == K]
ids = {int(r[c]) for r in rows for c in ('gold_badge_id', 'plain_badge_id')}
badges = {b.id: b for b in Badge.objects.filter(id__in=ids).prefetch_related('rules')}
class_badge_ids = {
    title: list(Badge.objects.filter(category__startswith='Course Completion /', title=title).values_list('id', flat=True))
    for title in {r['cls'] for r in rows}
}
out, clawed, board_clawed = collections.Counter(), 0, 0
for r in rows:
    gold, plain = badges[int(r['gold_badge_id'])], badges[int(r['plain_badge_id'])]
    [plain_rule] = list(plain.rules.all())
    with transaction.atomic():
        user = GammaUser.objects.filter(user_uid=r['username']).first()
        if user is None:
            out['no gamma user'] += 1
            continue
        user = GammaUser.objects.select_for_update().get(pk=user.pk)
        held = list(Achievement.objects.filter(
            user=user, content_type=badge_type, completed_at__isnull=False, object_id__in=class_badge_ids[r['cls']],
        ))
        if len(held) != 1:
            out['holds %d tiers' % len(held)] += 1
            print('SKIP %s %s holds %d tiers' % (r['username'], r['cls'], len(held)))
            continue
        achievement = held[0]
        if achievement.object_id == plain.id:
            out['keep'] += 1
            continue
        if achievement.object_id != gold.id:
            out['holds another tier'] += 1
            print('SKIP %s %s holds %s' % (r['username'], r['cls'], badges.get(achievement.object_id, achievement.object_id)))
            continue
        paid = achievement.completion_points_paid if achievement.completion_points_paid is not None else gold.points
        claw = max(0, paid - plain.points)
        achievement.object_id, achievement.title, achievement.description = plain.id, (plain.title or '')[:64], plain.description
        achievement.completion_points_paid = paid - claw
        achievement.save(update_fields=('object_id', 'title', 'description', 'completion_points_paid'))
        for link in AchievementRule.objects.filter(achievement=achievement):
            link.rule = plain_rule
            link.dependencies = dict(link.dependencies or {}, **{MARK: gold.id})
            link.save(update_fields=('rule', 'dependencies'))
        board_claw = 0
        if claw:
            user.update_user_points(-claw)
            user.update_user_progress(-claw)
            user.refresh_points_progress()
            board = GammaUserCoursePoints.objects.select_for_update().filter(gamma_user=user, course_id=r['cert_run']).first()
            if board is not None and board.points:
                board_claw = min(claw, board.points)
                board.points -= board_claw
                board.save(update_fields=('points',))
        leaderboards.schedule_user_leaderboards_update(user.user_uid)
        clawed += claw
        board_clawed += board_claw
        out['demoted'] += 1
        print('DEMOTED|%s|%s|%d|%d' % (r['username'], r['cls'], claw, board_claw))
print('SHARD %d/%d rows %d: %s; points clawed back %d; course-board points %d' % (
    K, N, len(rows), ', '.join('%s=%d' % kv for kv in sorted(out.items())), clawed, board_clawed))
