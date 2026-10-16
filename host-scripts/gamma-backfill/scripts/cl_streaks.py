"""
Continuous Learning — daily activity points and streak badges.

A learner has an "active day" whenever they are logged in and perform an action that
earns them points (submitting an answer, completing a unit, posting in the forum, …).
Because earning points requires an authenticated session, "earned points today" already
implies "logged in today", so an active day is detected simply by a point-earning event
arriving for the user (see ``rules.signals.process_event_creation``).

For every active day the learner is awarded :data:`DAILY_ACTIVE_POINTS` into a single
"Continuous Learning" bucket on their Points Distribution. The daily points have no
EventConfiguration (one config carries a single fixed award, but the bucket needs to
accept the plain daily amount), so they are written straight to the user's
totals/chart/progress via GammaUser helpers.

Active days also build *streaks*, of which there are two kinds (:data:`STREAK_KINDS`),
counted side by side from that same activity:

* **daily** — consecutive calendar days; badge "{N} day streak".
* **weekday** — consecutive weekdays (Mon–Fri); badge "{N} weekday streak". Saturdays and
  Sundays are invisible to this kind: weekend activity neither extends a run nor breaks
  one, so a learner who only studies on workdays carries a live streak across the
  weekend. Without it such a learner resets to 1 every Monday and can never pass 5,
  putting the 10/20/30 day-streak badges permanently out of reach.

The "{N} … streak" badges are ordinary rule-driven badges (one rule each, action
``{<the kind's event>: {count: N}}``) awarded by the rules engine — exactly like the
points-threshold badges. After a counter advances we emit that kind's internal event so
its rules re-evaluate against the counter: the dashboard then shows an in-progress ring
(streak / N) and the badge completes — paying its bonus points to the user's total and
points timeline via the normal completion path — when the streak reaches N.

All work runs under the per-user row lock already held by the event signal, so the
read-modify-write of the streak counters and points is safe against concurrent events.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Dict, Iterable, List, Optional, Tuple

from django.conf import settings

from events.enums import RggInternalEventTypes

# Single Points Distribution bucket that all Continuous Learning points accumulate into.
# The frontend chart is data-driven (bucket label = stored ``title``), so no UI change is
# needed for this bucket to appear.
CONTINUOUS_LEARNING_KEY = 'continuous_learning'
CONTINUOUS_LEARNING_TITLE = 'Continuous Learning'

# Free-text grouping label applied to the streak badges (Badge.category), used to group
# them together on the all-badges page. Both kinds of streak badge share it.
CONTINUOUS_LEARNING_CATEGORY = 'Continuous Learning'

# Points awarded for each active day.
DAILY_ACTIVE_POINTS = 5

# Milestones every streak kind offers: streak length -> bonus points configured as the
# matching badge's completion points (Badge.points), paid by the rules engine when the
# badge completes. Used by initialize_continuous_learning_badges to seed the badges and
# their rules.
STREAK_MILESTONES = (
    (5, 10),
    (10, 10),
    (20, 25),
    (30, 50),
)

# Events that credit points to a user *without that user being active that day* must not
# count toward an active day or a streak. ``edx_forum_thread_voted`` pays the post's
# author when someone *else* up-votes it, so the author may not have been present at all.
PASSIVE_EVENT_NAMES = frozenset({'edx_forum_thread_voted'})

# date.weekday() returns 0-6 for Mon-Sun, so 5 is the first day of the weekend.
SATURDAY = 5


@dataclass(frozen=True)
class StreakKind:
    """
    One way of counting a run of active days, with its own counter, badges and event.

    ``weekdays_only`` is the single behavioural switch. When set, weekend days are
    invisible to this kind — they neither extend a run nor break one — which makes the
    "day that must have been active for this one to continue the run" the previous
    *weekday*: Friday for a Monday, rather than Sunday.
    """

    key: str
    # GammaUser fields holding this kind's counter and the last day it counted.
    streak_field: str
    last_date_field: str
    # Internal event emitted to re-evaluate this kind's badge rules.
    event_type: RggInternalEventTypes
    # Wording of the badges: "{N} day streak" / "…for {N} consecutive days…".
    badge_noun: str
    unit_plural: str
    weekdays_only: bool

    @property
    def event_name(self) -> str:
        return self.event_type.value

    def counts(self, day: date) -> bool:
        """Whether activity on ``day`` is visible to this kind of streak at all."""
        return not (self.weekdays_only and day.weekday() >= SATURDAY)

    def previous_expected_day(self, day: date) -> date:
        """The day that must have been active for ``day`` to extend a run (Mon -> Fri)."""
        previous = day - timedelta(days=1)
        while not self.counts(previous):
            previous -= timedelta(days=1)
        return previous

    def next_countable_day(self, day: date) -> date:
        """The first day on or after ``day`` on which a run could be extended."""
        target = day
        while not self.counts(target):
            target += timedelta(days=1)
        return target

    def cutoff_for_broken_run(self, today: date) -> date:
        """
        The earliest last-active date that still leaves a run continuable on ``today``.

        A run survives exactly while its last active day is the one immediately preceding
        the next day this kind counts; anything earlier means a countable day was missed.
        For the weekday kind that look-ahead spans the weekend on its own — asked on a
        Saturday it is already aiming at Monday, so a learner last active on Friday is
        still live.
        """
        return self.previous_expected_day(self.next_countable_day(today))

    def advance(self, user, day: date) -> bool:
        """
        Move ``user``'s counter for this kind onto ``day``, in memory (the caller saves).

        Returns ``True`` when the counter changed — that is, when ``day`` is one this kind
        counts and has not been counted yet. A day following the previous expected one
        extends the run; anything else restarts it at 1.
        """
        if not self.counts(day):
            return False

        last_active = getattr(user, self.last_date_field)
        if last_active == day:
            return False

        if last_active == self.previous_expected_day(day):
            setattr(user, self.streak_field, getattr(user, self.streak_field) + 1)
        else:
            setattr(user, self.streak_field, 1)
        setattr(user, self.last_date_field, day)
        return True

    def badge_slug(self, days: int) -> str:
        """Stable slug used to look up / create a milestone badge (matches slugify of title)."""
        return f'{days}-{self.badge_noun.replace(" ", "-")}'

    def badge_title(self, days: int) -> str:
        return f'{days} {self.badge_noun}'

    def badge_description(self, days: int) -> str:
        return f'You were active for {days} consecutive {self.unit_plural} learning on the platform! Great job!'


DAILY_STREAK = StreakKind(
    key='daily',
    streak_field='current_streak',
    last_date_field='last_active_date',
    event_type=RggInternalEventTypes.RGG_CONTINUOUS_LEARNING_STREAK,
    badge_noun='day streak',
    unit_plural='days',
    weekdays_only=False,
)

WEEKDAY_STREAK = StreakKind(
    key='weekday',
    streak_field='current_weekday_streak',
    last_date_field='last_weekday_active_date',
    event_type=RggInternalEventTypes.RGG_CONTINUOUS_LEARNING_WEEKDAY_STREAK,
    badge_noun='weekday streak',
    unit_plural='weekdays',
    weekdays_only=True,
)

STREAK_KINDS: Tuple[StreakKind, ...] = (DAILY_STREAK, WEEKDAY_STREAK)


def is_enabled() -> bool:
    """Whether Continuous Learning processing is on (kill-switch, default on)."""
    return getattr(settings, 'RGG_CONTINUOUS_LEARNING_ENABLED', True)


def register_active_day(
    user,
    event_name: Optional[str] = None,
    activity_date: Optional[date] = None,
    force: bool = False,
) -> bool:
    """
    Record that ``user`` was active on ``activity_date`` (defaults to today) and, if this
    is the first qualifying activity of that day, award the daily points, advance every
    streak kind that counts the day and grant any milestone badges now due.

    ``event_name`` is the triggering event's type; passive events (see
    :data:`PASSIVE_EVENT_NAMES`) never count. ``force`` bypasses the feature kill-switch
    (for smoke-testing the engine on a box where the feature is switched off). Returns ``True``
    when the day was newly counted, ``False`` when it was a no-op (feature off, passive
    event, or already counted today). Idempotent within a calendar day.
    """
    if not force and not is_enabled():
        return False

    if event_name in PASSIVE_EVENT_NAMES:
        return False

    if activity_date is None:
        # Mirror update_user_progress's notion of "today" (naive server-local now) so the
        # streak day and the points-timeline entry always agree.
        activity_date = datetime.now().date()

    if user.last_active_date == activity_date:
        # Already counted today — don't double-award the daily points.
        return False

    # A weekday advances both kinds; a weekend day advances only the daily one, leaving
    # the weekday run untouched (neither extended nor broken) for Monday to pick up.
    advanced = [kind for kind in STREAK_KINDS if kind.advance(user, activity_date)]
    user.save(update_fields=_changed_fields(advanced))

    _award_continuous_learning_points(user, DAILY_ACTIVE_POINTS, activity_date)
    for kind in advanced:
        _trigger_streak_badge_evaluation(user, kind)
    return True


def _changed_fields(kinds: List[StreakKind]) -> Tuple[str, ...]:
    """The GammaUser fields written by advancing ``kinds`` (for a narrow update_fields)."""
    return tuple(field for kind in kinds for field in (kind.streak_field, kind.last_date_field))


def _award_continuous_learning_points(user, amount: int, when: date) -> None:
    """Add ``amount`` to the user's total, daily progress (on ``when``) and the bucket."""
    user.update_user_points(amount)
    user.update_user_progress(amount, when=when)
    user.add_chart_points(CONTINUOUS_LEARNING_KEY, CONTINUOUS_LEARNING_TITLE, amount)


def reset_stale_streaks(today: Optional[date] = None) -> int:
    """
    Zero out streaks that can no longer continue and refresh their badge rings.

    A run is broken once the learner has missed a day its kind counts — i.e. their last
    active date is earlier than :meth:`StreakKind.cutoff_for_broken_run`, so the next
    active day would restart it at 1 anyway. We zero those eagerly (and re-evaluate the
    badges to 0%) so a learner returning after a gap, before earning points, sees an
    accurate "streak broken" state rather than stale progress.

    A run that is still extendable today is deliberately left alone: zeroing it would
    corrupt the next increment. For the weekday kind that grace covers the weekend by
    construction — someone last active on Friday keeps their run through Saturday and
    Sunday and can extend it on Monday.

    Idempotent — only counters above 0 are touched, so a streak stays zeroed on later
    runs. Earned points/badges and the Continuous Learning bucket are untouched; only the
    counters and their in-progress rings reset. Returns the number of counters reset (a
    learner whose daily *and* weekday runs both broke counts twice). Run daily via
    ``users.tasks.reset_stale_continuous_learning_streaks``.
    """
    if not is_enabled():
        return 0

    from users.models import GammaUser

    if today is None:
        today = datetime.now().date()

    reset_count = 0
    for kind in STREAK_KINDS:
        stale_users = list(GammaUser.objects.filter(**{
            f'{kind.streak_field}__gt': 0,
            f'{kind.last_date_field}__lt': kind.cutoff_for_broken_run(today),
        }))
        for user in stale_users:
            setattr(user, kind.streak_field, 0)
            user.save(update_fields=(kind.streak_field,))
            # Re-evaluate this kind's badges against the now-zero streak: rings drop to 0%.
            _trigger_streak_badge_evaluation(user, kind)
        reset_count += len(stale_users)

    return reset_count


def _trigger_streak_badge_evaluation(user, kind: StreakKind) -> None:
    """
    Re-evaluate ``kind``'s badges against the user's just-updated counter.

    Emits the kind's internal event so the rules engine updates each badge's progress
    (streak / N) and awards it on reaching N — the same mechanism the points-threshold
    badges use. Guarded: if the event type has not been seeded yet
    (initialize_continuous_learning_badges), this is a no-op rather than raising and
    breaking event processing for everyone.
    """
    from events.models import EventConfiguration
    from events.utils import simulate_rgg_internal_event

    if EventConfiguration.objects.filter(event_type__name=kind.event_name).exists():
        simulate_rgg_internal_event(user, kind.event_name)


# Outcomes of a streak-badge backfill grant (see :func:`grant_streak_badge`).
GRANTED = 'granted'
ALREADY_HELD = 'already_held'
EXCLUDED = 'excluded'
OTHER_RULES = 'other_rules'


def milestones_reached(active_days: Iterable[date], kind: StreakKind) -> Dict[int, date]:
    """
    Map each streak milestone ``kind`` reached in ``active_days`` to the day it was first reached.

    Walks the days oldest first with the same continuation test the live engine applies
    (:meth:`StreakKind.previous_expected_day`), so the weekday kind ignores weekends here
    exactly as it does live. Only the *first* time a run reaches a milestone matters: that
    is the day the learner would have earned the badge had the feature always been on.
    """
    goals = sorted(days for days, _bonus in STREAK_MILESTONES)
    reached: Dict[int, date] = {}
    run, previous = 0, None
    for day in sorted(day for day in set(active_days) if kind.counts(day)):
        run = run + 1 if previous is not None and previous == kind.previous_expected_day(day) else 1
        previous = day
        if run in goals and run not in reached:
            reached[run] = day
            if len(reached) == len(goals):
                break
    return reached


def streak_badge_obstacle(user, badge, kind: StreakKind) -> Optional[str]:
    """
    Why ``badge`` cannot be backfilled to ``user`` (an outcome constant), or ``None`` if it can.

    Read-only, so a dry run can predict every outcome without writing anything.
    """
    from django.contrib.contenttypes.models import ContentType

    from achievements.models import Achievement

    if Achievement.objects.filter(
        user=user,
        content_type=ContentType.objects.get_for_model(type(badge)),
        object_id=badge.id,
        completed_at__isnull=False,
    ).exists():
        return ALREADY_HELD
    if any(rule.event_configuration.event_name != kind.event_name for rule in badge.rules.all()):
        # Somebody gave the badge a requirement beyond the streak; we cannot vouch for it.
        return OTHER_RULES
    if badge.blocking_badges(user).exists():
        return EXCLUDED
    return None


def grant_streak_badge(user, badge, kind: StreakKind, days: int, reached_on: date, silent: bool = True) -> str:
    """
    Award ``badge`` (the ``days``-long milestone of ``kind``) to ``user`` as if earned on ``reached_on``.

    Used by the ``backfill_continuous_learning`` command, which computes milestones from
    historical activity instead of replaying it. The result is indistinguishable from a
    rule-driven completion, so the live engine carries on normally afterwards:

    * Any in-progress Achievement (the ring the live engine keeps for every active learner)
      is completed in place; otherwise one is created with its AchievementRule rows, as the
      engine's create path would. ``Badge.award_to_user`` cannot be used for this: it
      skips anyone who already has a row, and nearly every active learner has one.
    * The streak rule is marked COMPLETED with its dependency at goal, dated ``reached_on``,
      so the engine never re-evaluates it (it only touches ACTIVE rules).
    * :class:`AchievementCompletionUseCase` dates the completion, pays ``Badge.points`` once,
      records ``completion_points_paid`` and emits the internal "achievement obtained" event.
    * ``silent`` marks the badge notification as seen, so learners are not toasted about a
      streak that ended long ago.

    The streak counters and the daily Continuous Learning points are left alone. Takes the
    user's row lock, so it is safe to run alongside live events and in parallel shards.
    Returns :data:`GRANTED` or the obstacle that prevented the grant.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.db import transaction
    from django.utils.timezone import now

    from achievements.models import Achievement, AchievementRule
    from achievements.usecases import AchievementCompletionUseCase
    from users.models import GammaUser

    with transaction.atomic():
        user = GammaUser.objects.select_for_update().get(pk=user.pk)
        if obstacle := streak_badge_obstacle(user, badge, kind):
            return obstacle

        # End of the UTC day the milestone was reached (never in the future).
        achieved_at = min(datetime.combine(reached_on, time(23, 59, 59), tzinfo=dt_timezone.utc), now())
        stamp = achieved_at.isoformat()

        achievement, _ = Achievement.objects.get_or_create(
            user=user,
            content_type=ContentType.objects.get_for_model(type(badge)),
            object_id=badge.id,
            defaults={'title': badge.title, 'description': badge.description},
        )
        for rule in badge.rules.all():
            AchievementRule.objects.update_or_create(
                achievement=achievement,
                rule=rule,
                defaults={
                    'status': AchievementRule.Statuses.COMPLETED,
                    'dependencies': {
                        'events': {kind.event_name: {
                            'goal': days, 'count': days, 'last_updated': stamp, 'achieved_at': stamp,
                        }},
                        'is_achieved': True,
                    },
                },
            )

        achievement.user = user
        AchievementCompletionUseCase().execute(achievement, completed_at=achieved_at)
        if silent:
            Achievement.objects.filter(pk=achievement.pk, notification_seen_at__isnull=True).update(
                notification_seen_at=now(),
            )
    return GRANTED
