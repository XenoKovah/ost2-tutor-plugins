"""
OST2 drop-in for gamma_bridge: backfill the profile-milestone events behind the
"Complete your profile" badges from each learner's CURRENT profile.

For every learner it builds one synthetic ``edx.user.settings.changed`` event per profile
setting and runs it through the bridge's own ProfileSettingStatement, so event types, uids
("<event_type>:<username>", one per learner per milestone) and the "is it populated?"
checks are exactly the live bridge's. Only settings that are set today produce an event.

Dates: the picture uses profile_image_uploaded_at; the other fields keep no change date in
the LMS, so those events carry none and Gamma dates them at insert time.

    ./manage.py lms backfill_profile_events [--dry-run] [--limit N]

Installed at run time (docker cp into gamma_bridge/management/commands/); no image rebuild.
"""
from collections import Counter, defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from gamma_bridge.statements.profile import PUBLIC_VISIBILITY_VALUE, ProfileSettingStatement, _is_set
from gamma_bridge.storage import GammaStorage


class Command(BaseCommand):
    help = 'Backfill profile-milestone events from current learner profiles.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', dest='dry_run')
        parser.add_argument('--limit', type=int, default=None, help='Stop after this many learners.')

    def handle(self, *args, **options):
        from common.djangoapps.student.models import LanguageProficiency, UserProfile
        from openedx.core.djangoapps.user_api.models import UserPreference

        conf = settings.FEATURES.get('RG_GAMIFICATION', {})
        storage = GammaStorage(enabled=conf.get('ENABLED'), endpoint=conf.get('RG_GAMIFICATION_ENDPOINT'),
                               secret=conf.get('SECRET'), key=conf.get('KEY'))
        if not (storage.is_enabled or options['dry_run']):
            raise CommandError('RG_GAMIFICATION is not enabled/configured in FEATURES.')

        name_public = set(UserPreference.objects.filter(key='visibility.name', value='all_users')
                          .values_list('user_id', flat=True))
        languages = defaultdict(list)
        for user_id, code in LanguageProficiency.objects.values_list('user_profile__user_id', 'code'):
            languages[user_id].append({'code': code})

        counters, per_type = Counter(), Counter()
        profiles = UserProfile.objects.select_related('user').order_by('user_id')
        for n, profile in enumerate(profiles.iterator(), start=1):
            if options['limit'] is not None and n > options['limit']:
                break
            user = profile.user
            current = {
                'visibility.name': 'all_users' if user.id in name_public else None,
                'profile_image_uploaded_at': profile.profile_image_uploaded_at,
                'level_of_education': profile.level_of_education,
                'country': str(profile.country) if profile.country else '',
                'language_proficiencies': languages.get(user.id, []),
                'bio': profile.bio,
            }
            for setting, value in current.items():
                # Cheap pre-check with the bridge's own predicates (the same test is_allowed_to_save
                # applies), so a statement (each costs a signup-source query) is built only for
                # settings that are actually populated.
                if setting == 'visibility.name':
                    populated = str(value).strip() == PUBLIC_VISIBILITY_VALUE
                else:
                    populated = _is_set(value)
                if not populated:
                    counters['skipped'] += 1
                    continue
                event = {
                    'event_type': 'edx.user.settings.changed',
                    'username': user.username,
                    'event': {'setting': setting, 'old': None, 'new': value,
                              'user_id': user.id, 'table': 'ost2_profile_backfill'},
                    'context': {'user_id': user.id},
                }
                statement = ProfileSettingStatement(event)
                if not statement.is_allowed_to_save(event):
                    counters['skipped'] += 1
                    continue
                payload = dict(statement.data)
                if setting == 'profile_image_uploaded_at' and value:
                    payload['created_at'] = value.isoformat()
                per_type[payload['event_type']] += 1
                if options['dry_run']:
                    counters['sent'] += 1
                    continue
                try:
                    storage.save(payload)
                except Exception as exc:  # noqa: B902 - per-row resilience, summarized at the end
                    self.stderr.write(f'ERROR {user.username}/{setting}: {exc}')
                    counters['errors'] += 1
                    continue
                code = storage.response_data.status_code
                counters['sent'] += 1
                if code == 201:
                    counters['created'] += 1
                elif code == 400 and 'unique set' in storage.response_data.text:
                    counters['duplicates'] += 1
                else:
                    counters['errors'] += 1
                    self.stderr.write(f'UNEXPECTED {code} for {user.username}/{setting}: {storage.response_data.text[:200]}')

        for event_type, count in sorted(per_type.items()):
            self.stdout.write(f'  {event_type}: {count}')
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(f"[dry-run] profile: would send {counters['sent']} ({counters['skipped']} skipped)."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"profile: sent {counters['sent']} ({counters['created']} created, {counters['duplicates']} duplicate, "
                f"{counters['errors']} error, {counters['skipped']} skipped)."))
