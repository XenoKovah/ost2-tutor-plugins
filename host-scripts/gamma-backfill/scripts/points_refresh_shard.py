"""
OST2 drop-in for gamma: re-evaluate the "Points points points!" badges (lifetime points
total >= N) of ONE shard of learners against their current total, so it can run as N
parallel processes.

For each learner it emits one internal ``rgg_points_distribution`` event: the event the
live pipeline emits after every point-earning event. The rings and grants therefore come
out exactly as if the learner had just earned points: each ring shows max(total, 0)/goal
and every threshold the total has reached is granted (dated now). A recompute
(backfill_recompute_shard) can't do this job: it only visits learners who already have a
points event or a Points achievement, which leaves out learners whose points all came
from backfills. Grants made here are silent (notification_seen_at set), like every other
backfill grant.

Needs gamma `points-rings-refresh` or later (rings floored at 0); refuses on an older one.

    python manage.py backfill_points_refresh_shard --shard 3/8 [--dry-run]

Installed at run time by run_points_refresh.sh (docker cp into badges/management/commands/).
"""
import inspect
from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.utils.timezone import now

from achievements.models import Achievement
from badges.models import Badge
from events.enums import RggInternalEventTypes
from events.processors import RggPointsDistributionProcessor
from events.utils import simulate_rgg_internal_event
from users.models import GammaUser

POINTS_EVENT = RggInternalEventTypes.RGG_POINTS_DISTRIBUTION.value


class Command(BaseCommand):
    help = 'Re-evaluate the points-total badges of one learner shard (GammaUser.id % N == k).'

    def add_arguments(self, parser):
        parser.add_argument('--shard', default='0/1', help='k/N (default 0/1 = everyone).')
        parser.add_argument('--dry-run', action='store_true', help='Report what would be granted; write nothing.')

    def handle(self, *args, **options):
        if 'max(points, 0)' not in inspect.getsource(RggPointsDistributionProcessor._process):
            raise CommandError('this gamma predates points-rings-refresh: rings of negative totals would go negative')
        try:
            k, n = (int(part) for part in options['shard'].split('/'))
        except ValueError:
            raise CommandError('shard must look like k/N')
        if n < 1 or not 0 <= k < n:
            raise CommandError('shard k must be in 0..N-1')

        badge_ct = ContentType.objects.get_for_model(Badge)
        goals = {}
        points_badges = Badge.objects.filter(is_active=True, rules__event_configuration__event_type__name=POINTS_EVENT)
        for badge in points_badges.distinct():
            rules = list(badge.rules.all())
            if len(rules) != 1:
                raise CommandError('badge %d %r has %d rules, expected one points rule'
                                   % (badge.id, badge.title, len(rules)))
            goals[badge.id] = int((rules[0].action or {})[POINTS_EVENT]['points'])
        if not goals:
            raise CommandError('no active points-total badges')

        granted = Counter()
        learners = current = 0
        users = [u for u in GammaUser.objects.only('id', 'user_uid', 'points').order_by('id') if u.id % n == k]
        for user in users:
            learners += 1
            achievements = {
                a.object_id: a for a in Achievement.objects.filter(
                    user=user, content_type=badge_ct, object_id__in=goals,
                ).prefetch_related('achievement_rules')
            }
            if all(self._is_current(achievements.get(b), goal, user.points) for b, goal in goals.items()):
                current += 1
                continue
            held = {b: a.id for b, a in achievements.items() if a.completed_at is not None}
            if options['dry_run']:
                granted.update(b for b, goal in goals.items() if user.points >= goal and b not in held)
                continue
            simulate_rgg_internal_event(user, POINTS_EVENT)
            new = Achievement.objects.filter(
                user=user, content_type=badge_ct, object_id__in=goals, completed_at__isnull=False,
            ).exclude(id__in=held.values())
            granted.update(new.values_list('object_id', flat=True))
            new.filter(notification_seen_at__isnull=True).update(notification_seen_at=now())

        titles = dict(Badge.objects.filter(id__in=goals).values_list('id', 'title'))
        for badge_id in sorted(goals, key=goals.get):
            self.stdout.write('  %-32s +%d' % (titles[badge_id][:32], granted[badge_id]))
        self.stdout.write(self.style.SUCCESS('shard %d/%d: %s%d learners (%d already current), +%d granted' % (
            k, n, 'DRY ' if options['dry_run'] else '', learners, current, sum(granted.values()))))

    @staticmethod
    def _is_current(achievement, goal, points):
        """Whether this badge already shows what an emission would write: held, or a ring at max(points, 0)."""
        if achievement is None:
            return False
        if achievement.completed_at is not None:
            return True
        rule_links = list(achievement.achievement_rules.all())
        if len(rule_links) != 1:
            return False
        progress = ((rule_links[0].dependencies or {}).get('events') or {}).get(POINTS_EVENT) or {}
        return points < goal and progress.get('count') == max(points, 0) and progress.get('goal') == goal
