"""
OST2 drop-in for gamma: reconcile the holders of a category's rule-driven badges for ONE
shard of learners, so recompute can run as N parallel processes.

Same per-(badge, learner) reconciliation as achievements.reconciliation.recompute_holders,
but learners are split by GammaUser.id modulo N, and each learner's work runs under that
learner's row lock — the lock the live event signal takes — so a learner is never written
by two processes (or by a live event) at once.

    python manage.py backfill_recompute_shard --category "Using The Site" --shard 3/8
    python manage.py backfill_recompute_shard --rebuild-leaderboard

Installed at run time by recompute_backfill.sh (docker cp into badges/management/commands/).
"""
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F

from achievements.models import Achievement
from achievements.reconciliation import AchievementReconciliationService, RecomputeResult, _candidate_users
from badges.models import Badge
from users.models import GammaUser


class Command(BaseCommand):
    help = 'Reconcile badge holders of a category for one learner shard (GammaUser.id % N == k).'

    def add_arguments(self, parser):
        parser.add_argument('--category', help='Badge category to reconcile.')
        parser.add_argument('--shard', default='0/1', help='k/N (default 0/1 = everyone).')
        parser.add_argument('--rebuild-leaderboard', action='store_true',
                            help='Only schedule the leaderboard rebuild (run once, after all shards).')

    def handle(self, *args, **options):
        if options['rebuild_leaderboard']:
            from leaderboard.repository import ORMLeaderboardMemberDataRepository
            from leaderboard.usecases import (
                ResetLeaderboardsInitializationStatusUseCase,
                ScheduleLeaderboardsInitializationUseCase,
            )
            ResetLeaderboardsInitializationStatusUseCase().execute()
            ScheduleLeaderboardsInitializationUseCase(ORMLeaderboardMemberDataRepository()).execute(
                settings.LEADERBOARD_INITIALIZATION_BATCH_SIZE)
            self.stdout.write(self.style.SUCCESS('Leaderboard rebuild scheduled (rgg-worker will process it).'))
            return

        if not options['category']:
            raise CommandError('--category is required')
        try:
            k, n = (int(part) for part in options['shard'].split('/'))
        except ValueError:
            raise CommandError('shard must look like k/N')
        if n < 1 or not 0 <= k < n:
            raise CommandError('shard k must be in 0..N-1')

        service = AchievementReconciliationService()
        badge_ct = ContentType.objects.get_for_model(Badge)
        # Same selection as backfill_recompute: every badge in the category that has rules.
        badges = [b for b in Badge.objects.filter(category=options['category']) if b.rules.exists()]
        granted = revoked = 0
        for badge in badges:
            users = [u for u in _candidate_users(badge, list(badge.rules.all())) if u.id % n == k]
            result = RecomputeResult()
            for user in users:
                with transaction.atomic():
                    locked = GammaUser.objects.select_for_update().get(pk=user.pk)
                    outcome = service.reconcile_user(badge, locked)
                    result.record(outcome, locked)
                    if outcome == 'granted':
                        # Backfill grants are silent (decided 2026-10-02): no badge-earned toast.
                        Achievement.objects.filter(
                            user=locked, content_type=badge_ct, object_id=badge.id, notification_seen_at__isnull=True,
                        ).update(notification_seen_at=F('completed_at'))
            granted += len(result.granted)
            revoked += len(result.revoked)
            self.stdout.write('  %-32s %s' % ((badge.title or '')[:32], result.summary()))
        self.stdout.write(self.style.SUCCESS(
            'shard %d/%d (%s): +%d granted, -%d revoked' % (k, n, options['category'], granted, revoked)))
