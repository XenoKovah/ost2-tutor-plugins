"""
DROP-IN COPY (RGG/backfills/scripts/cl_backfill_cmd.py) of gamma users/management/commands/backfill_continuous_learning.py
@ b4aa2b3, run beside the deployed code from /tmp/clbf. Changes: imports the streak module as `cl_streaks`
(a copy of users/continuous_learning.py @ b4aa2b3), and adds --ignore-event-type and --ignore-events-file.

Backfill the Continuous Learning streak badges from each learner's historical activity.

OPTIONAL. The live feature starts every learner's streak fresh from the day it is
enabled. Run this command to also credit the streaks learners already had: it derives
each learner's active days (distinct UTC days on which they earned points from a
non-passive action) straight from the Event table, works out which "{N} day streak" and
"{N} weekday streak" milestones those days reached, and grants each such badge once,
dated the day the milestone was first reached. It does **not** replay events.

What it touches, per granted badge (see users.continuous_learning.grant_streak_badge):
the learner's Achievement for that badge (completed in place if the live engine already
keeps an in-progress ring for it), the badge's completion points (Badge.points, paid once
by the normal completion path) and the internal "achievement obtained" event.

What it leaves alone: the streak counters (current_streak / current_weekday_streak and
their last-active dates) and the daily +5 Continuous Learning points. The live engine
keeps tracking today's streaks exactly as before.

Safety and scale:
  * Dry-run by default: read-only, prints what would be granted. ``--commit`` to apply.
  * Idempotent: a badge the learner already holds (live or from an earlier run) is
    skipped, so re-running, or running over learners the live feature has credited, is safe.
  * ``--shard k/N`` restricts the run to learners with ``GammaUser.id % N == k``, so N
    processes can run side by side over disjoint learners. Each grant takes the learner's
    row lock, so shards and live events never interleave on the same learner.
  * Work is organized per learner, not per event: one grouped query per batch of
    learners returns their (learner, day) pairs, so cost scales with learners and
    active days rather than with the (much larger) number of events.
  * Grants are silent (no "badge earned" toast) unless ``--notify`` is passed.
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Mod, TruncDate

from badges.models import Badge
from events.models import Event, EventConfiguration
from cl_streaks import (
    GRANTED,
    PASSIVE_EVENT_NAMES,
    STREAK_KINDS,
    STREAK_MILESTONES,
    grant_streak_badge,
    milestones_reached,
    streak_badge_obstacle,
)
from users.models import GammaUser

WOULD_GRANT = 'would_grant'
MISSING_BADGE = 'missing_badge'


class Command(BaseCommand):
    help = 'Grant Continuous Learning streak badges earned by historical activity (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit', action='store_true',
            help='Grant the badges. Without it, the command only reports (read-only).',
        )
        parser.add_argument(
            '--shard', default='0/1',
            help='k/N: only learners with GammaUser.id %% N == k (default 0/1 = everyone).',
        )
        parser.add_argument(
            '--batch-size', type=int, default=1000,
            help='Learners whose active days are fetched per query (default 1000).',
        )
        parser.add_argument(
            '--user-uid', dest='user_uid', default=None,
            help='Limit to a single GammaUser by user_uid (useful for testing).',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Process at most N learners of the shard (useful for a staged rollout).',
        )
        parser.add_argument(
            '--notify', action='store_true',
            help='Leave the badge-earned notification pending instead of marking it seen.',
        )
        parser.add_argument(
            '--ignore-event-type', dest='ignore_event_types', action='append', default=[],
            help='Event type that never counts as an active day (repeatable): for events whose stored date '
                 'is not a day the learner was active, e.g. profile fields a backfill dated at import time.',
        )
        parser.add_argument(
            '--ignore-events-file', default=None,
            help='File of Event ids (one per line) that never count as an active day, e.g. enrollment events '
                 'for enrollments that staff or a script created rather than the learner.',
        )
        parser.add_argument(
            '--verbose-users', action='store_true',
            help='Print one line per learner with a milestone (default: summary only).',
        )

    def handle(self, *args, **options):
        shard_k, shard_n = self._parse_shard(options['shard'])
        if options['batch_size'] < 1:
            raise CommandError('--batch-size must be at least 1')
        commit = options['commit']

        badges = self._streak_badges()
        ignored = set(options['ignore_event_types'])
        unknown = ignored - set(EventConfiguration.common_event_names())
        if unknown:
            raise CommandError('--ignore-event-type: not a common event type: %s' % ', '.join(sorted(unknown)))
        common_names = sorted(set(EventConfiguration.common_event_names()) - PASSIVE_EVENT_NAMES - ignored)
        ignore_ids = set()
        if options['ignore_events_file']:
            ignore_ids = {int(line) for line in open(options['ignore_events_file']) if line.strip()}
        self.stdout.write('Active days from: %s; ignoring %d listed events' % (', '.join(common_names), len(ignore_ids)))

        users = GammaUser.objects.annotate(_shard=Mod(F('id'), shard_n)).filter(_shard=shard_k).order_by('id')
        if options['user_uid']:
            users = users.filter(user_uid=options['user_uid'])
        if options['limit'] is not None:
            users = users[:options['limit']]

        outcomes = Counter()  # (kind, days, outcome) -> learners
        scanned = with_milestone = 0
        for batch in self._batches(users, options['batch_size']):
            active_days = self._active_days(batch, common_names, ignore_ids)
            for user in batch:
                scanned += 1
                days = active_days.get(user.user_uid)
                if not days:
                    continue
                reached = {kind: milestones_reached(days, kind) for kind in STREAK_KINDS}
                if not any(reached.values()):
                    continue
                with_milestone += 1
                user_outcomes = self._settle_user(user, reached, badges, commit, silent=not options['notify'])
                outcomes.update(user_outcomes)
                if options['verbose_users']:
                    summary = ', '.join(f'{kind}:{days}={outcome}' for kind, days, outcome in user_outcomes)
                    self.stdout.write(f'{user.user_uid}: {len(days)} active days; {summary}')

        self._report(outcomes, scanned, with_milestone, shard_k, shard_n, commit)

    @staticmethod
    def _parse_shard(spec):
        try:
            k, n = (int(part) for part in spec.split('/'))
        except ValueError:
            raise CommandError('--shard must look like k/N, e.g. 3/8')
        if n < 1 or not 0 <= k < n:
            raise CommandError('--shard k must be in 0..N-1')
        return k, n

    def _streak_badges(self):
        """The active milestone badges, keyed by (kind key, days). Missing ones are reported, not fatal."""
        badges = {}
        for kind in STREAK_KINDS:
            for days, _bonus in STREAK_MILESTONES:
                badge = Badge.objects.filter(slug=kind.badge_slug(days), is_active=True).prefetch_related(
                    'rules__event_configuration__event_type',
                ).first()
                if badge is None:
                    self.stderr.write(self.style.WARNING(
                        f'No active badge {kind.badge_slug(days)!r}; run initialize_continuous_learning_badges.'
                    ))
                badges[(kind.key, days)] = badge
        return badges

    @staticmethod
    def _batches(users, size):
        batch = []
        for user in users.only('id', 'user_uid').iterator(chunk_size=size):
            batch.append(user)
            if len(batch) == size:
                yield batch
                batch = []
        if batch:
            yield batch

    @staticmethod
    def _active_days(batch, common_names, ignore_ids=frozenset()):
        """{user_uid: set of UTC days with a point-earning, non-passive event} for one batch."""
        events = Event.objects.filter(
            username__in=[user.user_uid for user in batch], configuration__event_type__name__in=common_names,
        )
        if ignore_ids:
            events = events.exclude(id__in=ignore_ids)
        rows = (
            events
            .annotate(day=TruncDate('created_at'))
            .values_list('username', 'day')
            .distinct()
        )
        days = defaultdict(set)
        for username, day in rows.iterator():
            days[username].add(day)
        return days

    @staticmethod
    def _settle_user(user, reached, badges, commit, silent):
        """Grant (or, in a dry run, predict) every milestone one learner reached. All-or-nothing per learner."""
        results = []
        with transaction.atomic():
            for kind, milestones in reached.items():
                for days, reached_on in sorted(milestones.items()):
                    badge = badges[(kind.key, days)]
                    if badge is None:
                        outcome = MISSING_BADGE
                    elif commit:
                        outcome = grant_streak_badge(user, badge, kind, days, reached_on, silent=silent)
                    else:
                        outcome = streak_badge_obstacle(user, badge, kind) or WOULD_GRANT
                    results.append((kind.key, days, outcome))
        return results

    def _report(self, outcomes, scanned, with_milestone, shard_k, shard_n, commit):
        mode = 'COMMITTED' if commit else 'DRY-RUN (nothing written)'
        self.stdout.write(f'\n{mode}: shard {shard_k}/{shard_n}; scanned {scanned} learners, '
                          f'{with_milestone} reached at least one milestone.')
        for kind in STREAK_KINDS:
            for days, bonus in STREAK_MILESTONES:
                counts = {outcome: n for (k, d, outcome), n in outcomes.items() if k == kind.key and d == days}
                if counts:
                    detail = ', '.join(f'{outcome}={n}' for outcome, n in sorted(counts.items()))
                    self.stdout.write(f'  {kind.badge_slug(days):20s} (+{bonus} pts): {detail}')
        granted = sum(n for (_k, _d, outcome), n in outcomes.items() if outcome in (GRANTED, WOULD_GRANT))
        self.stdout.write(self.style.SUCCESS(f'{"Granted" if commit else "Would grant"} {granted} badges.'))
        if not commit and granted:
            self.stdout.write('Re-run with --commit to apply.')
