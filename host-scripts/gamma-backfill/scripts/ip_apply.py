"""
Set each instructor's LMS profile picture from the photo in their course About page (decided 2026-10-02),
so leaderboards show their faces. Runs in the LMS shell with /tmp/instructor_photos.tsv (username, asset,
source_course, name, matched_by). DRY RUN unless APPLY=1.

Per row: skip anyone who already has a profile picture (theirs stays). The asset column names the photo:
`asset-v1:...` (this site's course asset), `file:<name>` (a photo approved for use and kept in
manual/instructor_photos/, copied to /tmp/ip_photos/), or `profile:<username>` (that account's current
picture, for the same person's second account). Anything else, e.g. an external URL, is skipped: approve it
and store it as a file first. A photo over the 1 MB upload limit is shrunk to 1000 px first. Then and go through the same steps as the profile-image upload view: validate_uploaded_image,
create_profile_images for every size, then set_has_profile_image. Prints one line per row:
SET / WOULD SET / SKIP <reason>.
"""
import io
import mimetypes
import os
from datetime import datetime, timezone

from PIL import Image

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from opaque_keys.edx.keys import AssetKey
from openedx.core.djangoapps.profile_images.images import create_profile_images, validate_uploaded_image
from openedx.core.djangoapps.user_api.accounts.image_helpers import (
    get_profile_image_names, get_profile_image_storage, set_has_profile_image,
)
from xmodule.contentstore.django import contentstore

APPLY = os.environ.get('APPLY') == '1'
MAX_BYTES = 1024 * 1024
SHRINK_TO = 1000
store = contentstore()
for line in open('/tmp/instructor_photos.tsv').read().splitlines()[1:]:
    username, asset, course, name, how = line.split('\t')
    user = User.objects.filter(username=username).select_related('profile').first()
    if user is None:
        print('SKIP %s: no such account' % username)
        continue
    if user.profile.profile_image_uploaded_at:
        print('SKIP %s: already has a profile picture (%s)' % (username, user.profile.profile_image_uploaded_at.date()))
        continue
    if asset.startswith('file:'):
        # A photo approved for use and kept with the plan (manual/instructor_photos/), e.g. one hosted elsewhere.
        filename = asset[len('file:'):]
        data, ctype = open('/tmp/ip_photos/' + filename, 'rb').read(), mimetypes.guess_type(filename)[0] or 'image/jpeg'
    elif asset.startswith('profile:'):
        # The same person's other account: copy its current picture (the largest size the LMS keeps).
        source = asset[len('profile:'):]
        owner = User.objects.filter(username=source).select_related('profile').first()
        if owner is None or not owner.profile.profile_image_uploaded_at:
            print('SKIP %s: %s has no profile picture to copy' % (username, source))
            continue
        largest = max(get_profile_image_names(source).items())[1]
        data, filename, ctype = get_profile_image_storage().open(largest).read(), largest, 'image/jpeg'
    elif asset.startswith('asset-v1:'):
        try:
            content = store.find(AssetKey.from_string(asset))
        except Exception as error:  # pylint: disable=broad-except
            print('SKIP %s: asset not found here (%s: %s)' % (username, asset, type(error).__name__))
            continue
        data, filename, ctype = content.data, asset.rsplit('@', 1)[-1], content.content_type
    else:
        print('SKIP %s: photo is not a course asset on this site (%s)' % (username, asset))
        continue
    original_size, note = len(data), ''
    if len(data) > MAX_BYTES:
        # Over the LMS's upload limit: shrink first. The LMS keeps at most a 500 px square anyway.
        image = Image.open(io.BytesIO(data))
        image.thumbnail((SHRINK_TO, SHRINK_TO))
        out = io.BytesIO()
        image.convert('RGB').save(out, 'JPEG', quality=90)
        data, filename, ctype = out.getvalue(), filename.rsplit('.', 1)[0] + '.jpg', 'image/jpeg'
        note = ' (shrunk from %d to %d bytes)' % (original_size, len(data))
    photo = SimpleUploadedFile(filename, data, content_type=ctype)
    try:
        validate_uploaded_image(photo)
    except Exception as error:  # pylint: disable=broad-except
        print('SKIP %s: %s rejected by the LMS (%s)' % (username, filename, error))
        continue
    if not APPLY:
        print('WOULD SET %s <- %s (%d bytes, %s)%s for %s' % (username, filename, len(data), ctype, note, name))
        continue
    photo.seek(0)
    create_profile_images(photo, get_profile_image_names(username))
    set_has_profile_image(username, True, datetime.now(timezone.utc).replace(microsecond=0))
    print('SET %s <- %s%s for %s' % (username, filename, note, name))
