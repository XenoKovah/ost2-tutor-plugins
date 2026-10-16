"""
Set instructors' LMS profile names to their real names (decided 2026-10-02), from manual/instructor_names.tsv
(username, name, source) copied to /tmp/instructor_names.tsv. Runs in the LMS shell; DRY RUN unless APPLY=1.
Uses the account API's own update (update_account_settings: validation, and the old name kept in the profile's
name history). Prints SET / WOULD SET / SKIP per row.
"""
import os

from django.contrib.auth.models import User
from openedx.core.djangoapps.user_api.accounts.api import update_account_settings

APPLY = os.environ.get('APPLY') == '1'
for line in open('/tmp/instructor_names.tsv', encoding='utf-8').read().splitlines()[1:]:
    username, name, source = line.split('\t')
    user = User.objects.filter(username=username).select_related('profile').first()
    if user is None:
        print('SKIP %s: no such account' % username)
        continue
    old = user.profile.name
    if old == name:
        print('SKIP %s: already %r' % (username, name))
        continue
    if not APPLY:
        print('WOULD SET %s: %r -> %r (%s)' % (username, old, name, source))
        continue
    update_account_settings(user, {'name': name})
    user.profile.refresh_from_db()
    print('SET %s: %r -> %r' % (username, old, user.profile.name))
