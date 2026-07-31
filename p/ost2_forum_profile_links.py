"""
Tutor plugin: ost2_forum_profile_links

Adds a "(profile)" link after the author's name on forum posts, in BOTH places
this Teak (Forum v2) deployment renders a discussion author:

  1. The standalone Discussions MFE (frontend-app-discussions) -- e.g.
     https://apps.p.ost2.fyi/discussions/<course>/posts/<id>
  2. The legacy inline DiscussionXBlock rendered by the LMS below each
     courseware unit (and embedded in the Learning MFE unit page) -- the same
     surface patched by the ost2_inline_discussion_fix plugin.

Both links point at the learner's public profile in the Profile MFE
(<PROFILE_MFE_BASE>/u/<username>).

Why two patches
---------------
The two surfaces render the author in completely different code, and neither
exposes a frontend-plugin-framework slot near the author, so each is a small,
fail-loud, build-time source patch (same approach as ost2_inline_discussion_fix):

  * MFE: frontend-app-discussions funnels every post AND comment author through
    one component, src/discussions/common/AuthorLabel.jsx. We append a
    "(profile)" link there, built with getConfig().ACCOUNT_PROFILE_URL (the same
    config key frontend-component-header uses for its "Profile" menu item). The
    existing author name keeps its in-MFE "learner's posts" link. The anchor
    stops click propagation: post-list cards (PostLink.jsx) wrap the whole
    summary -- author included -- in a react-router <Link> whose handler
    preventDefault()s the bubbled click and re-navigates to the post, which
    otherwise silently swallows (profile) clicks in every posts list.

  * Legacy: edx-platform funnels every post/response/comment/endorser author
    through one underscore template, #post-user-display-template in
    common/static/common/templates/discussion/templates.underscore. There the
    author name currently links to /courses/<course>/discussion/forum/users/<id>
    which 500s on Forum v2 (the legacy forum user page was removed). We BOTH
    repoint that broken username link to the Profile MFE AND append a "(profile)"
    link next to it. The legacy template is client-side JS with no MFE config, so
    the absolute profile base URL (PROFILE_MFE_BASE) is baked in at build time.

PROFILE_MFE_BASE
----------------
Must equal this deployment's ACCOUNT_PROFILE_URL / PROFILE_MICROFRONTEND_URL
*without* the trailing "/u/". For p.ost2.fyi that is the value below. If you
ever change the MFE host, update this one constant.

Safety
------
Every edit asserts its anchor appears exactly once and aborts the image build
otherwise -- so if an edx-platform / frontend-app-discussions upgrade moves the
code, the build fails loudly instead of silently shipping an unpatched image.
Each patch is idempotent (re-running no-ops once applied).

Install
-------
    cp ost2_forum_profile_links.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_forum_profile_links
    tutor config save
    tutor images build openedx mfe          # both images change
    tutor local restart
"""
import base64
import json

from tutor import hooks

__version__ = "1.1.0"

# ---------------------------------------------------------------------------
# Deployment-specific: base URL of the Profile MFE (no trailing "/u/").
# Equals ACCOUNT_PROFILE_URL in the MFE config and PROFILE_MICROFRONTEND_URL
# (minus "/u/") in the LMS settings.
# ---------------------------------------------------------------------------
PROFILE_MFE_BASE = "https://apps.p.ost2.fyi/profile"
_PROFILE_PREFIX = PROFILE_MFE_BASE + "/u/"

########################################################################
# Patch 1 -- Discussions MFE: src/discussions/common/AuthorLabel.jsx
# Runs at the `pre-npm-build-discussions` hook: AFTER the app source is copied
# in (`COPY --from=discussions-src / /openedx/app`) and BEFORE `npm run build`.
# (The earlier `post-npm-install` hook only has package.json -- the source tree
# isn't there yet -- so AuthorLabel.jsx wouldn't exist to patch.) The MFE image
# is node-based (no python), so this patch is a Node.js script.
########################################################################

# JSX inserted just before the component's `return`, defining the link element.
_MFE_PROFILE_LINK_CONST = (
    "  const profileLink = (author && !isRetiredUser\n"
    "    && author !== intl.formatMessage(messages.anonymous)) ? (\n"
    "      <a\n"
    "        href={`${getConfig().ACCOUNT_PROFILE_URL}/u/${author}`}\n"
    "        className=\"mr-1.5 author-profile-link\"\n"
    "        data-testid=\"author-profile-link\"\n"
    "        onClick={(e) => e.stopPropagation()}\n"
    "      >\n"
    "        (profile)\n"
    "      </a>\n"
    "    ) : null;"
)

# (anchor, replacement) pairs. Each anchor must occur exactly once.
_MFE_EDITS = [
    # 1) import getConfig (only useIntl is imported today)
    [
        "import { useIntl } from '@edx/frontend-platform/i18n';",
        "import { getConfig } from '@edx/frontend-platform';\n"
        "import { useIntl } from '@edx/frontend-platform/i18n';",
    ],
    # 2) define profileLink just before the return
    [
        "  return showUserNameAsLink",
        _MFE_PROFILE_LINK_CONST + "\n\n  return showUserNameAsLink",
    ],
    # 3) name-is-a-link branch: render it after the existing learner-posts <Link>
    [
        "        </Link>\n        {labelContents}",
        "        </Link>\n        {!alert && profileLink}\n        {labelContents}",
    ],
    # 4) name-is-plain-text branch (anonymous-in-sidebar etc.)
    [
        ">{authorName}{labelContents}</div>",
        ">{authorName}{profileLink}{labelContents}</div>",
    ],
]

_MFE_CANDIDATES = [
    "src/discussions/common/AuthorLabel.jsx",
    "/openedx/app/src/discussions/common/AuthorLabel.jsx",
]
_MFE_MARKER = "author-profile-link"

_MFE_PATCH_SCRIPT = (
    "const fs = require('fs');\n"
    "const EDITS = " + json.dumps(_MFE_EDITS) + ";\n"
    "const MARKER = " + json.dumps(_MFE_MARKER) + ";\n"
    "const CANDIDATES = " + json.dumps(_MFE_CANDIDATES) + ";\n"
    "const file = CANDIDATES.find((p) => fs.existsSync(p));\n"
    "if (!file) {\n"
    "  console.error('ost2 FATAL: AuthorLabel.jsx not found in ' + CANDIDATES.join(', '));\n"
    "  process.exit(1);\n"
    "}\n"
    "let src = fs.readFileSync(file, 'utf8');\n"
    "if (src.includes(MARKER)) {\n"
    "  console.log('ost2: discussions MFE (profile) link already applied; skipping');\n"
    "  process.exit(0);\n"
    "}\n"
    "for (const [anchor, replacement] of EDITS) {\n"
    "  const n = src.split(anchor).length - 1;\n"
    "  if (n !== 1) {\n"
    "    console.error('ost2 FATAL: expected exactly 1 occurrence of anchor, found ' + n + ' -- AuthorLabel.jsx changed? Anchor: ' + JSON.stringify(anchor.slice(0, 80)));\n"
    "    process.exit(1);\n"
    "  }\n"
    "  src = src.replace(anchor, replacement);\n"
    "}\n"
    "fs.writeFileSync(file, src);\n"
    "console.log('ost2: applied (profile) link to discussions MFE AuthorLabel.jsx (' + file + ')');\n"
)

########################################################################
# Patch 2 -- legacy template: templates.underscore (openedx image)
# Runs inside the openedx image build (production stage, python available),
# before assets are compiled.
########################################################################

_LEGACY_PATH = (
    "/openedx/edx-platform/common/static/common/templates/discussion"
    "/templates.underscore"
)
# The single author hyperlink shared by posts, responses, comments and
# endorsers (#post-user-display-template). user_url == the 500-ing legacy URL.
_LEGACY_ANCHOR = '<a href="<%- user_url %>" class="username"><%- username %></a>'
_LEGACY_REPLACEMENT = (
    '<a href="' + _PROFILE_PREFIX + '<%- username %>" class="username">'
    '<%- username %></a>'
    '<a href="' + _PROFILE_PREFIX + '<%- username %>" '
    'class="username ost2-profile-link" style="margin-left:.25rem">(profile)</a>'
)
_LEGACY_MARKER = "ost2-profile-link"

_LEGACY_PATCH_SCRIPT = (
    "import sys\n"
    "PATH = " + json.dumps(_LEGACY_PATH) + "\n"
    "ANCHOR = " + json.dumps(_LEGACY_ANCHOR) + "\n"
    "REPLACEMENT = " + json.dumps(_LEGACY_REPLACEMENT) + "\n"
    "MARKER = " + json.dumps(_LEGACY_MARKER) + "\n"
    "with open(PATH, encoding='utf-8') as fh:\n"
    "    src = fh.read()\n"
    "if MARKER in src:\n"
    "    print('ost2: legacy discussion (profile) link already applied; skipping')\n"
    "    sys.exit(0)\n"
    "n = src.count(ANCHOR)\n"
    "if n != 1:\n"
    "    sys.stderr.write(\n"
    "        'ost2 FATAL: expected exactly 1 author-link anchor in %s, found %d.\\n'\n"
    "        'The legacy discussion template changed (upgraded? fixed upstream?).\\n'\n"
    "        'Review/remove the ost2_forum_profile_links Tutor plugin.\\n' % (PATH, n)\n"
    "    )\n"
    "    sys.exit(1)\n"
    "with open(PATH, 'w', encoding='utf-8') as fh:\n"
    "    fh.write(src.replace(ANCHOR, REPLACEMENT, 1))\n"
    "print('ost2: applied legacy discussion (profile) link to ' + PATH)\n"
)

########################################################################
# Register both patches as single base64 RUN lines (no heredoc / quoting
# hazards), mirroring ost2_inline_discussion_fix.
########################################################################

_B64_MFE = base64.b64encode(_MFE_PATCH_SCRIPT.encode("utf-8")).decode("ascii")
_B64_LEGACY = base64.b64encode(_LEGACY_PATCH_SCRIPT.encode("utf-8")).decode("ascii")

hooks.Filters.ENV_PATCHES.add_items(
    [
        (
            "mfe-dockerfile-pre-npm-build-discussions",
            "# OST2: forum author '(profile)' link -- Discussions MFE AuthorLabel.jsx\n"
            # Decode to a real file before running: `node /dev/stdin` fails on
            # Node 20 (the module loader realpath-resolves the stdin pipe and
            # then can't readFileSync the pipe inode -> ENOENT).
            "RUN echo " + _B64_MFE + " | base64 -d > /tmp/ost2_authorlabel_patch.js"
            " && node /tmp/ost2_authorlabel_patch.js"
            " && rm -f /tmp/ost2_authorlabel_patch.js\n",
        ),
        (
            "openedx-dockerfile-pre-assets",
            "# OST2: forum author '(profile)' link -- legacy inline discussion template\n"
            "RUN echo " + _B64_LEGACY + " | base64 -d | python -\n",
        ),
    ]
)
