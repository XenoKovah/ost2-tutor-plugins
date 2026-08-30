"""
Tutor plugin: ost2_markdown_xblock_parse_xml

Fixes: an OLX course import on Teak SILENTLY drops every Markdown component.

markdown-xblock 1.4.0 (hastexo) declares

    @classmethod
    def parse_xml(cls, node, runtime, keys, id_generator):

but XBlock 5.2.0 (shipped with Teak) removed the `id_generator` argument from
the parse_xml() contract. Every <markdown ... /> element in an imported course
therefore raises

    TypeError: MarkdownXBlock.parse_xml() missing 1 required positional
               argument: 'id_generator'

xmodule/vertical_block.py catches that and only logs "Unable to load child when
parsing Vertical. Continuing...", so the import reports SUCCESS while every
Markdown block is quietly discarded. Observed 2026-08-30 importing two courses
from beta to dev: 10 markdown blocks became 0, and 35 became 0.

Only XML import is affected -- parse_xml() is not used for rendering, Studio
editing or export -- so it bites on course import, course rerun, and any
export/import round trip.

The patch makes `id_generator` optional and derives the resource path from
`url_name` when it is absent. Upstream only used `id_generator` to compute
`base`, which is always the literal "markdown" directory, so this is behaviour
preserving. The embedded script is idempotent and FAILS THE IMAGE BUILD loudly
if markdown-xblock's source ever stops matching its anchors, rather than
silently no-op'ing and leaving the bug in place.

Hook choice: `openedx-dockerfile`, not `openedx-dockerfile-post-python-
requirements`. The latter renders BEFORE the OPENEDX_EXTRA_PIP_REQUIREMENTS
loop that installs markdown-xblock, so the file would not exist yet.
`openedx-dockerfile` sits at the end of the `production` stage, after the venv
is COPYed in and while USER is `app` (who owns the venv); `development` and
`final` both derive FROM production, so one patch covers every image variant.

REQUIRES AN IMAGE REBUILD (it changes site-packages inside the image):
    cp ost2_markdown_xblock_parse_xml.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_markdown_xblock_parse_xml
    tutor config save
    tutor images build openedx
    tutor local start -d

Durable fix belongs upstream (hastexo/markdown-xblock); drop this plugin if a
release ever supports XBlock 5.
"""
from tutor import hooks

__version__ = "1.0.0"

_PATCH_B64 = (
    "IiIiTWFrZSBtYXJrZG93bi14YmxvY2sncyBNYXJrZG93blhCbG9jay5wYXJzZV94bWwoKSB3b3JrIG9uIFhCbG9jayA+PSA1LgoKWEJsb2NrIDUueCBkcm9wcGVkIHRoZSBgaWRfZ2VuZXJhdG9yYCBhcmd1bWVudCBmcm9tIHBhcnNlX3htbCgpLCBidXQKbWFya2Rvd24teGJsb2NrIDEuNC4wIHN0aWxsIGRlY2xhcmVzIGl0IGFzIHJlcXVpcmVkLCBzbyBldmVyeSA8bWFya2Rvd24+CmVsZW1lbnQgcmFpc2VzIFR5cGVFcnJvciBkdXJpbmcgT0xYIGltcG9ydC4gIFZlcnRpY2FsQmxvY2sgY2F0Y2hlcyB0aGF0IGFuZApsb2dzICJVbmFibGUgdG8gbG9hZCBjaGlsZCB3aGVuIHBhcnNpbmcgVmVydGljYWwuIENvbnRpbnVpbmcuLi4iLCBzbyB0aGUKY29tcG9uZW50IGlzIGRyb3BwZWQgU0lMRU5UTFkgLS0gYSBjb3Vyc2UgaW1wb3J0IGxvc2VzIGV2ZXJ5IE1hcmtkb3duIGJsb2NrLgoKYGlkX2dlbmVyYXRvcmAgd2FzIG9ubHkgdXNlZCB0byBkZXJpdmUgYGJhc2VgLCB3aGljaCBpcyBhbHdheXMgdGhlIGxpdGVyYWwKIm1hcmtkb3duIiBkaXJlY3RvcnksIHNvIGRlcml2aW5nIGl0IGZyb20gdXJsX25hbWUgaW5zdGVhZCBpcyBiZWhhdmlvdXIKcHJlc2VydmluZy4gIElkZW1wb3RlbnQ7IGZhaWxzIHRoZSBpbWFnZSBidWlsZCBsb3VkbHkgaWYgdGhlIHVwc3RyZWFtIHNvdXJjZQpldmVyIHN0b3BzIG1hdGNoaW5nLgoiIiIKaW1wb3J0IGltcG9ydGxpYi51dGlsCmltcG9ydCBvcwppbXBvcnQgcHlfY29tcGlsZQppbXBvcnQgc3lzCgpUQUcgPSAib3N0Ml9tYXJrZG93bl94YmxvY2tfcGFyc2VfeG1sIgoKc3BlYyA9IGltcG9ydGxpYi51dGlsLmZpbmRfc3BlYygibWFya2Rvd25feGJsb2NrIikKaWYgc3BlYyBpcyBOb25lIG9yIG5vdCBzcGVjLm9yaWdpbjoKICAgIHN5cy5leGl0KGYie1RBR306IEZBSUxFRCAtIG1hcmtkb3duX3hibG9jayBpcyBub3QgaW5zdGFsbGVkIikKcGF0aCA9IG9zLnBhdGguam9pbihvcy5wYXRoLmRpcm5hbWUoc3BlYy5vcmlnaW4pLCAiaHRtbC5weSIpCgp3aXRoIG9wZW4ocGF0aCwgZW5jb2Rpbmc9InV0Zi04IikgYXMgZmg6CiAgICBzcmMgPSBmaC5yZWFkKCkKCk9MRF9TSUcgPSAiZGVmIHBhcnNlX3htbChjbHMsIG5vZGUsIHJ1bnRpbWUsIGtleXMsIGlkX2dlbmVyYXRvcik6IgpORVdfU0lHID0gImRlZiBwYXJzZV94bWwoY2xzLCBub2RlLCBydW50aW1lLCBrZXlzLCBpZF9nZW5lcmF0b3I9Tm9uZSk6IgpPTERfTE9DID0gIiAgICAgICAgbG9jYXRpb24gPSBpZF9nZW5lcmF0b3IuY3JlYXRlX2RlZmluaXRpb24obm9kZS50YWcsIHVybF9uYW1lKVxuIgpORVdfTE9DID0gKCIgICAgICAgIGxvY2F0aW9uID0gKGlkX2dlbmVyYXRvci5jcmVhdGVfZGVmaW5pdGlvbihub2RlLnRhZywgdXJsX25hbWUpXG4iCiAgICAgICAgICAgIiAgICAgICAgICAgICAgICAgICAgaWYgaWRfZ2VuZXJhdG9yIGlzIG5vdCBOb25lIGVsc2UgTm9uZSlcbiIpCk9MRF9QVEggPSAiICAgICAgICAgICAgdXJsX3BhdGg9bG9jYXRpb24uYmxvY2tfaWQucmVwbGFjZSgnOicsICcvJykiCk5FV19QVEggPSAoIiAgICAgICAgICAgIHVybF9wYXRoPShsb2NhdGlvbi5ibG9ja19pZCBpZiBsb2NhdGlvbiBpcyBub3QgTm9uZVxuIgogICAgICAgICAgICIgICAgICAgICAgICAgICAgICAgICAgZWxzZSB1cmxfbmFtZSkucmVwbGFjZSgnOicsICcvJykiKQoKaWYgTkVXX1NJRyBpbiBzcmM6CiAgICBwcmludChmIntUQUd9OiBhbHJlYWR5IHBhdGNoZWQsIG5vdGhpbmcgdG8gZG8gKHtwYXRofSkiKQogICAgc3lzLmV4aXQoMCkKCmZvciBhbmNob3IgaW4gKE9MRF9TSUcsIE9MRF9MT0MsIE9MRF9QVEgpOgogICAgaWYgc3JjLmNvdW50KGFuY2hvcikgIT0gMToKICAgICAgICBzeXMuZXhpdChmIntUQUd9OiBGQUlMRUQgLSBleHBlY3RlZCBleGFjdGx5IG9uZSBvY2N1cnJlbmNlIG9mIHthbmNob3Ihcn0sICIKICAgICAgICAgICAgICAgICBmImZvdW5kIHtzcmMuY291bnQoYW5jaG9yKX0gaW4ge3BhdGh9LiBtYXJrZG93bi14YmxvY2sgY2hhbmdlZDsgIgogICAgICAgICAgICAgICAgIGYicmUtY2hlY2sgdGhpcyBwYXRjaCBiZWZvcmUgc2hpcHBpbmcuIikKCnNyYyA9IHNyYy5yZXBsYWNlKE9MRF9TSUcsIE5FV19TSUcpLnJlcGxhY2UoT0xEX0xPQywgTkVXX0xPQykucmVwbGFjZShPTERfUFRILCBORVdfUFRIKQp3aXRoIG9wZW4ocGF0aCwgInciLCBlbmNvZGluZz0idXRmLTgiKSBhcyBmaDoKICAgIGZoLndyaXRlKHNyYykKcHlfY29tcGlsZS5jb21waWxlKHBhdGgsIGRvcmFpc2U9VHJ1ZSkKcHJpbnQoZiJ7VEFHfTogcGF0Y2hlZCB7cGF0aH0iKQo="
)

hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile",
    "# OST2: make markdown-xblock parse_xml() compatible with XBlock >= 5\n"
    "RUN echo '" + _PATCH_B64 + "' | base64 -d > /tmp/ost2_markdown_xblock_parse_xml.py \\\n"
    "    && python /tmp/ost2_markdown_xblock_parse_xml.py \\\n"
    "    && rm /tmp/ost2_markdown_xblock_parse_xml.py",
))
