"""
Tutor plugin: ost2_default_grading_policy

Make a NEWLY CREATED course come up with OST2's house grading criteria instead
of the Open edX stock one, and make every new subsection graded as
"Progress Marker" without the author having to set it one at a time.

This is the grading setup documented in the InstructorHowTo class
(.../InstructorHowTo+V1 -> "Grading"), which until now had to be rebuilt by hand
on every new class: delete 2 of the 4 stock assignment types, rename the other
2, and drag the pass/fail divider over to 99.

WHAT CHANGES

  Assignment Types (Studio -> Settings -> Grading)
      Progress Marker / ProgressMarker   weight 100   total number 1   droppable 0
      Timing Feedback / TimingFeedback   weight   0   total number 1   droppable 0
  Overall Grade Range
      Fail 0-99, Pass 99-100        (GRADE_CUTOFFS Pass = 0.99)
  New subsections
      created graded, "Grade as: Progress Marker"

HOW

  1. edx-platform declares CourseFields.grading_policy with
     default=DEFAULT_GRADING_POLICY (xmodule/course_metadata_utils.py), and
     XBlock's Field keeps that exact object in Field._default -- Field.default
     hands out a deepcopy of it. So mutating DEFAULT_GRADING_POLICY IN PLACE
     re-defaults the field. Applied via openedx-common-settings so the CMS (the
     Grading page) and the LMS (grade computation, certificates) agree.
     xmodule.course_metadata_utils is a leaf module -- stdlib + pytz/dateutil,
     no Django models -- so importing it at settings-evaluation time is safe.

  2. Every Studio "New subsection" (legacy outline, Authoring MFE, and the v0
     CMS REST API) funnels through
     contentstore.xblock_storage_handlers.create_xblock.create_xblock, so that
     is wrapped to call CourseGradingModel.update_section_grader_type() -- the
     exact call the "Grade as" dropdown makes -- on each new sequential.
     The wrapper only fires when the course's OWN grading policy actually
     contains a Progress Marker type, and reuses that course's spelling of it
     (older OST2 classes use "ProgressMarker", no space). A course still on the
     stock Homework/Lab/Midterm/Final policy is left alone, so a new subsection
     can never end up with an assignment type the course does not define.
     Course IMPORT does not go through create_xblock, so imported classes keep
     the grading that shipped in their OLX.

BLAST RADIUS
  A course that has never saved a grading policy falls back to the field
  default, so it picks this up too. On dev that was 4 courses, none of which has
  a single graded subsection (computed grade 0.0 either way, so no certificate
  behaviour changes): Lab_Setup_x86-64_Windows, YourClassHere, deleteme1234,
  asdf1234. All 41 real classes have an explicitly saved policy and are
  untouched.

IMPLEMENTATION NOTES
  * tutor renders ENV_PATCHES through Jinja, so the settings code below contains
    NO "{" / "}" literals -- every dict is built with dict().
  * The create_xblock wrapper is installed lazily on the first request (one-shot
    request_started signal) so Django's app registry is fully populated before
    contentstore / modulestore are imported; importing them at settings
    evaluation time would race app loading.
  * Both the defining module attribute and the name view_handlers.py bound at
    import time are replaced.

Settings-only change -- NO image rebuild (the Open edX settings dir is
bind-mounted into the containers):
    cp ost2_default_grading_policy.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_default_grading_policy
    tutor config save
    tutor local restart lms cms
"""
from tutor import hooks

__version__ = "1.0.0"


# ---------------------------------------------------------------------------
# LMS + CMS: the grading policy a course gets when it has never saved one.
# ---------------------------------------------------------------------------
_COMMON_SETTINGS = '''
# OST2 house grading criteria -- see the InstructorHowTo class, "Grading".
OST2_DEFAULT_GRADER_TYPE = "Progress Marker"


def _ost2_build_default_grading_policy():
    # "weight" is the fraction, not the percent the Studio UI shows:
    # 1.0 == "Weight of Total Grade: 100", 0.0 == "0".
    progress_marker = dict(
        type="Progress Marker",
        short_label="ProgressMarker",
        min_count=1,
        drop_count=0,
        weight=1.0,
    )
    timing_feedback = dict(
        type="Timing Feedback",
        short_label="TimingFeedback",
        min_count=1,
        drop_count=0,
        weight=0.0,
    )
    # Pass=0.99 draws the Overall Grade Range as Fail 0-99 / Pass 99-100.
    return dict(
        GRADER=[progress_marker, timing_feedback],
        GRADE_CUTOFFS=dict(Pass=0.99),
    )


# CourseFields.grading_policy holds this very object as Field._default, so
# mutate it in place rather than rebinding the name.
import xmodule.course_metadata_utils as _ost2_course_metadata_utils

_ost2_course_metadata_utils.DEFAULT_GRADING_POLICY.clear()
_ost2_course_metadata_utils.DEFAULT_GRADING_POLICY.update(
    _ost2_build_default_grading_policy()
)
del _ost2_course_metadata_utils
'''


# ---------------------------------------------------------------------------
# CMS only: a new subsection is created graded as "Progress Marker".
# ---------------------------------------------------------------------------
_CMS_SETTINGS = '''
# OST2: default every new Studio subsection to the Progress Marker assignment
# type, installed after app-loading via a one-shot request_started signal.
def _ost2_install_default_section_grader(sender=None, **kwargs):
    import logging

    from django.core.signals import request_started

    request_started.disconnect(_ost2_install_default_section_grader)

    from cms.djangoapps.contentstore.xblock_storage_handlers import (
        create_xblock as _ost2_create_xblock_module,
    )
    from cms.djangoapps.contentstore.xblock_storage_handlers import (
        view_handlers as _ost2_view_handlers,
    )
    from cms.djangoapps.models.settings.course_grading import CourseGradingModel
    from xmodule.modulestore.django import modulestore

    _ost2_log = logging.getLogger("ost2.default_grading_policy")
    _ost2_original_create_xblock = _ost2_create_xblock_module.create_xblock

    def _ost2_course_grader_type(course):
        # The course's own spelling of the default assignment type, or None when
        # it does not define one (e.g. a course still on the stock
        # Homework / Lab / Midterm / Final policy). Older OST2 classes spell it
        # "ProgressMarker", newer ones "Progress Marker".
        wanted = OST2_DEFAULT_GRADER_TYPE.replace(" ", "").lower()
        for grader in course.raw_grader or []:
            name = grader.get("type") or ""
            if name.replace(" ", "").lower() == wanted:
                return name
        return None

    def create_xblock(parent_locator, user, category, display_name,
                      boilerplate=None, is_entrance_exam=False):
        block = _ost2_original_create_xblock(
            parent_locator, user, category, display_name, boilerplate,
            is_entrance_exam,
        )
        if category == "sequential" and not is_entrance_exam:
            try:
                if not block.graded:
                    course = modulestore().get_course(block.location.course_key)
                    grader_type = _ost2_course_grader_type(course) if course else None
                    if grader_type:
                        # Same call the Studio "Grade as" dropdown makes: sets
                        # format + graded, saves, and fires the grading-changed
                        # signal so grades are recomputed.
                        CourseGradingModel.update_section_grader_type(
                            block, grader_type, user
                        )
            except Exception:  # pylint: disable=broad-except
                _ost2_log.exception(
                    "OST2: could not default new subsection %s to the %s "
                    "assignment type",
                    block.location,
                    OST2_DEFAULT_GRADER_TYPE,
                )
        return block

    _ost2_create_xblock_module.create_xblock = create_xblock
    _ost2_view_handlers.create_xblock = create_xblock
    _ost2_log.info(
        "OST2: new subsections now default to the %s assignment type",
        OST2_DEFAULT_GRADER_TYPE,
    )


from django.core.signals import request_started as _ost2_request_started

_ost2_request_started.connect(_ost2_install_default_section_grader)
'''


hooks.Filters.ENV_PATCHES.add_item(
    ("openedx-common-settings", _COMMON_SETTINGS)
)
hooks.Filters.ENV_PATCHES.add_item(
    ("openedx-cms-common-settings", _CMS_SETTINGS)
)
