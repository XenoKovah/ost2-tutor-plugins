"""
Read-only, in the LMS shell: list every instructor shown in a course About page ("Course Staff"
section: name + photo) and the LMS account it most likely belongs to. Prints TSV to stdout:
course_id, name, photo, username, match, has_image, candidates
match = how the account was found: 'team name' (the course's own staff/instructor role holder with
that profile name), 'handle' (an @handle in the bio that is a course-team username), 'site name'
(the only active account on the site with that exact profile name), or '' (not found).
"""
import html as htmlmod
import re
import sys
import unicodedata

from django.contrib.auth.models import User
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from common.djangoapps.student.models import CourseAccessRole
from xmodule.modulestore.django import modulestore

ARTICLE = re.compile(r'<article[^>]*class="[^"]*teacher[^"]*"[^>]*>(.*?)</article>', re.S | re.I)
IMG = re.compile(r'<img[^>]*src="([^"]+)"', re.I)
NAME = re.compile(r'<h[2-4][^>]*>(.*?)</h[2-4]>', re.S | re.I)
HANDLE = re.compile(r'@([A-Za-z0-9_.-]+)')
TAG = re.compile(r'<[^>]+>')


def norm(s):
    s = unicodedata.normalize('NFKD', htmlmod.unescape(TAG.sub('', s or ''))).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z ]', '', s.lower()).split()


store = modulestore()
by_name = {}
for u in User.objects.filter(is_active=True).select_related('profile').only('username', 'profile__name'):
    try:
        by_name.setdefault(' '.join(norm(u.profile.name)), []).append(u)
    except Exception:
        pass
print('course_id\tname\tphoto\tusername\tmatch\thas_image\tcandidates')
for co in CourseOverview.objects.order_by('id'):
    try:
        about = store.get_item(co.id.make_usage_key('about', 'overview')).data or ''
    except Exception:
        continue
    team = {r.user for r in CourseAccessRole.objects.filter(course_id=co.id, role__in=('instructor', 'staff')).select_related('user__profile')}
    team_by_name = {' '.join(norm(getattr(getattr(u, 'profile', None), 'name', ''))): u for u in team}
    team_by_username = {u.username.lower(): u for u in team}
    for art in ARTICLE.findall(about):
        names = NAME.findall(art)
        img = IMG.findall(art)
        name = htmlmod.unescape(TAG.sub('', names[0])).strip() if names else ''
        photo = img[0] if img else ''
        key = ' '.join(norm(name))
        user, how = None, ''
        if key and key in team_by_name:
            user, how = team_by_name[key], 'team name'
        if user is None:
            for h in HANDLE.findall(art):
                if h.lower() in team_by_username:
                    user, how = team_by_username[h.lower()], 'handle'
                    break
        site = by_name.get(key, []) if key else []
        if user is None and len(site) == 1:
            user, how = site[0], 'site name'
        has_image = bool(user and getattr(user.profile, 'profile_image_uploaded_at', None))
        cands = ','.join(sorted(u.username for u in site))[:200]
        print('\t'.join((str(co.id), name, photo, user.username if user else '', how, str(int(has_image)), cands)))
