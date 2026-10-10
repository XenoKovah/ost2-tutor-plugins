"""
Build the MFE image on a Debian *bookworm* node base instead of bullseye.

tutor-mfe's Dockerfile template hardcodes `node:<ver>-bullseye-slim`. Debian purged the
bullseye-security pool when bullseye went EOL (2026), so any MFE build whose `base` layer is
not already in the BuildKit cache dies in `apt install` with 404s on perl-base/libc6. The
template has no patch point before FROM, and hand-editing the rendered Dockerfile is wiped by
every `tutor config save`, so this plugin overrides the template itself.

At load time it reads tutor-mfe's own Dockerfile template, swaps bullseye->bookworm and the
bookworm-less `python` apt package for `python3`, writes the result to a private template
root, and puts that root FIRST in ENV_TEMPLATE_ROOTS so it shadows only that one file. Because
it is derived from the installed tutor-mfe template it tracks upstream changes; if the strings
it expects disappear (e.g. tutor-mfe fixes the base image) it raises, so the plugin gets
retired deliberately instead of silently doing nothing.
"""
from pathlib import Path

import tutormfe
from tutor import hooks

__version__ = "0.1.0"

_REL = Path("mfe") / "build" / "mfe" / "Dockerfile"
_SRC = Path(tutormfe.__file__).parent / "templates" / _REL
_ROOT = Path(__file__).resolve().parent / ".ost2_mfe_bookworm_templates"


def _patched(text):
    if "-bullseye-slim" not in text or "    python g++ \\" not in text:
        raise RuntimeError(
            "ost2_mfe_bookworm_base: tutor-mfe Dockerfile template no longer has the expected "
            "bullseye/python lines; review and retire or update this plugin"
        )
    return text.replace("-bullseye-slim", "-bookworm-slim").replace(
        "    python g++ \\", "    python3 g++ \\"
    )


def _write_override():
    out = _ROOT / _REL
    new = _patched(_SRC.read_text())
    if not out.exists() or out.read_text() != new:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(new)


_write_override()


@hooks.Filters.ENV_TEMPLATE_ROOTS.add()
def _shadow_mfe_dockerfile(roots):
    return [str(_ROOT)] + [r for r in roots if str(r) != str(_ROOT)]
