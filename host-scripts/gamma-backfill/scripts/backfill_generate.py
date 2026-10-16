"""
OST2 drop-in for gamma_bridge: generate the events a backfill feed would send to Gamma,
WITHOUT sending them. Each payload goes into a staging table in the LMS database for a bulk
import into Gamma (import_backfill_events.sh), followed by a recompute.

It runs the unmodified feed command with GammaStorage.save swapped for a capture, so the
payloads (uid, dates, block ids) are exactly what the replay — and, for live events, the
live bridge — produce. Duplicates of live events are therefore skipped on import.

    ./manage.py lms backfill_generate --create-table
    ./manage.py lms backfill_generate backfill_done_events --shard 3/8
    ./manage.py lms backfill_generate backfill_forum_events

Installed at run time by run_backfill_fast.sh (docker cp into
gamma_bridge/management/commands/), so it needs no image rebuild.
"""
from datetime import timezone as dt_timezone
from importlib import import_module

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import F
from django.db.models.functions import Mod
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from gamma_bridge.storage import GammaStorage

FEEDS = (
    'backfill_enrollment_events',
    'backfill_bookmark_events',
    'backfill_forum_events',
    'backfill_problem_submitted_events',
    'backfill_done_events',
    'backfill_block_completion_events',
    'backfill_profile_events',   # OST2 drop-in (backfill_profile_events.py)
)
# Learner FK column on each shardable feed's source model (the forum feed reads Mongo).
USER_FIELD = {
    'backfill_enrollment_events': 'user_id',
    'backfill_bookmark_events': 'user_id',
    'backfill_block_completion_events': 'user_id',
    'backfill_problem_submitted_events': 'student_id',
    'backfill_done_events': 'student_id',
}
COLUMNS = ('uid', 'signup_source', 'user_uid', 'created_at', 'client', 'org', 'course_id', 'block_id', 'event_type')


class _Created:
    """Stands in for Gamma's HTTP response so the feed counts every capture as created."""
    status_code = 201
    text = ''


class Command(BaseCommand):
    help = 'Capture a gamma_bridge backfill feed into a staging table instead of sending it.'

    def add_arguments(self, parser):
        parser.add_argument('feed', nargs='?', choices=FEEDS)
        parser.add_argument('--shard', default=None, help='k/N: only learners with user id %% N == k.')
        parser.add_argument('--table', default='ost2_backfill_events')
        parser.add_argument('--client', default='main', help="Gamma AppClient name the bridge posts as.")
        parser.add_argument('--create-table', action='store_true', help='Create (or empty) the staging table.')
        parser.add_argument('--batch', type=int, default=2000)
        parser.add_argument('--collation', default='utf8mb4_unicode_ci',
                            help="Must match Gamma's events_event columns so the import can join on them.")

    def handle(self, *args, **options):
        table = connection.ops.quote_name(options['table'])
        if options['create_table']:
            with connection.cursor() as cur:
                cur.execute(f'DROP TABLE IF EXISTS {table}')
                cur.execute(f"""CREATE TABLE {table} (
                    uid varchar(255) NOT NULL, signup_source varchar(255) NULL, user_uid varchar(255) NOT NULL,
                    created_at datetime(6) NOT NULL, client varchar(255) NOT NULL, org varchar(255) NULL,
                    course_id varchar(255) NULL, block_id varchar(255) NULL, event_type varchar(255) NOT NULL,
                    UNIQUE KEY uid_client_user (uid, client, user_uid)
                ) DEFAULT CHARSET=utf8mb4 COLLATE={options['collation']}""")
            self.stdout.write(f'staging table {table} ready')
            if not options['feed']:
                return
        feed = options['feed']
        if not feed:
            raise CommandError('give a feed (or only --create-table)')

        command = import_module(f'gamma_bridge.management.commands.{feed}').Command
        if options['shard']:
            if feed not in USER_FIELD:
                raise CommandError(f'{feed} cannot be sharded')
            k, n = (int(part) for part in options['shard'].split('/'))
            if n < 1 or not 0 <= k < n:
                raise CommandError('shard must be k/N with k in 0..N-1')
            field = USER_FIELD[feed]
            base = command

            class ShardedFeed(base):
                CHECKPOINT_NAME = f'{base.CHECKPOINT_NAME}.shard{k}of{n}'

                def get_queryset(self, opts):
                    return super().get_queryset(opts).annotate(_ost2_shard=Mod(F(field), n)).filter(_ost2_shard=k)

            command = ShardedFeed

        client, batch, rows = options['client'], options['batch'], []
        insert = (f'INSERT IGNORE INTO {table} ({", ".join(COLUMNS)}) '
                  f'VALUES ({", ".join(["%s"] * len(COLUMNS))})')

        def flush():
            if rows:
                with connection.cursor() as cur:
                    cur.executemany(insert, rows)
                rows.clear()

        def capture(storage, payload):
            when = payload.get('created_at')
            moment = parse_datetime(when) if when else timezone.now()
            moment = moment.astimezone(dt_timezone.utc).replace(tzinfo=None)
            rows.append((payload['uid'], payload.get('signup_source'), payload['username'], moment, client,
                         payload.get('org'), payload.get('course_id'), payload.get('block_id'), payload['event_type']))
            storage.response_data = _Created()
            if len(rows) >= batch:
                flush()

        original_save = GammaStorage.save
        GammaStorage.save = capture
        try:
            call_command(command(stdout=self.stdout, stderr=self.stderr))
            flush()
        finally:
            GammaStorage.save = original_save
