"""
Tutor plugin: ost2_handouts  (CONSOLIDATED openedx-image wiring)

Single plugin replacing the four separate openedx-image handouts wiring plugins:
  * ost2_handouts_app_install   -> pip-install the ost2-handouts-app package
  * ost2_handouts_default_off   -> platform default = disabled (LMS+CMS settings)
  * ost2_handouts_learner_gate  -> LMS outline/views.py gate (pre-assets sed)
  * ost2_handouts_studio_gate   -> CMS studio block-info gate (pre-assets sed)

The two course-authoring MFE halves (former ost2_handouts_settings_ui and
ost2_handouts_studio_gate_authoring) are folded into the
XenoKovah/frontend-app-authoring fork instead, so they ship from MFE source.

The four sections below are the original plugins' bodies verbatim, each
self-contained (defines its patch content, then registers its ENV_PATCHES hook).
Repeated `import base64` / `from tutor import hooks` / `__version__` and the
per-section docstrings are intentional and harmless.
"""
"""
Tutor plugin: ost2_handouts_app_install

Installs the `ost2-handouts-app` package (the HandoutsCourseApp course-app
plugin + its is_handouts_enabled helper) into the openedx image, so:
  * Studio "Pages & Resources" lists a "Course Handouts" on/off toggle
    (the `handouts` openedx.course_app entry point), and
  * the gating plugins (ost2_handouts_learner_gate / ost2_handouts_studio_gate)
    can `from ost2_handouts_app.api import is_handouts_enabled`.

The package source is baked in (base64) and pip-installed at the
`openedx-dockerfile-post-python-requirements` build stage (the same stage
tutor-contrib-rgg uses for pip installs). `--no-deps`: every import the package
needs (edx_django_utils, opaque_keys, the course_apps app) is already provided
by edx-platform; we must not let pip try to resolve/upgrade platform pins.

This generated plugin embeds the package; if you edit
/Users/user/Downloads/RGG/ost2_handouts_app/*, regenerate it with
_dryrun_workspace/gen_installer.py. (Alternatively, publish the package to a
git repo and `RUN pip install git+https://...#egg=ost2-handouts-app` instead --
mirrors how the MFE Dockerfile installs frontend-rgg-widgets.)

Enable this BEFORE the two gating plugins so the import target exists at build.

Install
-------
    cp ost2_handouts_app_install.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_handouts_app_install
    tutor config save
    tutor images build openedx
    tutor local restart lms cms lms-worker cms-worker
"""
import base64

from tutor import hooks

__version__ = "1.0.0"

_B64 = "RklMRVMgPSB7J3NldHVwLnB5JzogJ0lpSWlDbTl6ZERKZmFHRnVaRzkxZEhOZllYQndJT0tBbENCaElFTnZkWEp6WlNCQmNIQWdjR3gxWjJsdUlIUm9ZWFFnWVdSa2N5QmhJSEJsY2kxamIzVnljMlVnSWtOdmRYSnpaUXBJWVc1a2IzVjBjeUlnZEc5bloyeGxJSFJ2SUZOMGRXUnBieWR6SUNKUVlXZGxjeUFtSUZKbGMyOTFjbU5sY3lJZ2NHRm5aUzRLQ2xKbFoybHpkR1Z5Y3lCaGJpQmdiM0JsYm1Wa2VDNWpiM1Z5YzJWZllYQndZQ0JsYm5SeWVTQndiMmx1ZENCemJ5QjBhR1VnWTI5MWNuTmxMV0YxZEdodmNtbHVaeUJOUmtVZ2JHbHpkSE1LYVhRZ1lYVjBiMjFoZEdsallXeHNlU0FvZEdoaGRDQndZV2RsSUhKbGJtUmxjbk1nZDJoaGRHVjJaWElLWUM5aGNHa3ZZMjkxY25ObFgyRndjSE12ZGpFdllYQndjeTk3WTI5MWNuTmxYMmxrZldBZ2NtVjBkWEp1Y3lrdUlFVnVZV0pzWldRdGMzUmhkR1VnYVhNZ2MzUnZjbVZrSUdsdUlIUm9aUXB3YkdGMFptOXliU2R6SUdkbGJtVnlhV01nWUVOdmRYSnpaVUZ3Y0ZOMFlYUjFjMkFnYlc5a1pXd2dLSFJvWlNCellXMWxJSE4wYjNKbElHTmhiR04xYkdGMGIzSXZkR1ZoYlhNZ2RYTmxDbmRvWlc0Z2RHaGxlU0JrYjI0bmRDQnJaV1Z3SUhSb1pXbHlJRzkzYmlCemRHRjBaU2tzSUhOdklHNXZJR1Y0ZEhKaElHMXBaM0poZEdsdmJuTWdZWEpsSUc1bFpXUmxaQzRLQ2tSbFptRjFiSFE2SUVWT1FVSk1SVVFnS0hCeVpYTmxjblpsY3lCamRYSnlaVzUwSUdKbGFHRjJhVzl5S1M0Z1ZHaGxJSEJsY2kxamIzVnljMlVnZEc5bloyeGxJR3hsZEhNZ2MzUmhabVlLWkdsellXSnNaU0JvWVc1a2IzVjBjenNnWjJGMGFXNW5JRzltSUhSb1pTQnNaV0Z5Ym1WeUlHTnZkWEp6WlMxb2IyMWxJR0Z1WkNCMGFHVWdVM1IxWkdsdklGVndaR0YwWlhNS2FHRnVaRzkxZEhNZ1pXUnBkRzl5SUdseklHUnZibVVnWW5rZ1kyOXRjR0Z1YVc5dUlIUjFkRzl5SUhCc2RXZHBibk1nS0hObFpTQlNSVUZFVFVVZ2FXNGdkR2hwY3lCa2FYSXBMZ3BVYUdVZ2NHeGhkR1p2Y20wdGQybGtaU0JrWldaaGRXeDBJR05oYmlCaVpTQm1iR2x3Y0dWa0lFOUdSaUIzYVhSb0lHRWdjMmx1WjJ4bElFUnFZVzVuYnlCelpYUjBhVzVuQ2loUFUxUXlYMGhCVGtSUFZWUlRYMFJGUmtGVlRGUmZSVTVCUWt4RlJDQTlJRVpoYkhObEtUc2djMlZsSUdoaGJtUnZkWFJ6WDJGd2NDOXdiSFZuYVc1ekxuQjVMZ29pSWlJS1puSnZiU0J6WlhSMWNIUnZiMnh6SUdsdGNHOXlkQ0J6WlhSMWNDd2dabWx1WkY5d1lXTnJZV2RsY3dvS2MyVjBkWEFvQ2lBZ0lDQnVZVzFsUFNKdmMzUXlMV2hoYm1SdmRYUnpMV0Z3Y0NJc0NpQWdJQ0IyWlhKemFXOXVQU0l4TGpBdU1DSXNDaUFnSUNCa1pYTmpjbWx3ZEdsdmJqMGlUMU5VTWpvZ2NHVnlMV052ZFhKelpTQkRiM1Z5YzJVZ1NHRnVaRzkxZEhNZ2RHOW5aMnhsSUNoUGNHVnVJR1ZrV0NCRGIzVnljMlVnUVhCd0lIQnNkV2RwYmlraUxBb2dJQ0FnY0dGamEyRm5aWE05Wm1sdVpGOXdZV05yWVdkbGN5Z3BMQW9nSUNBZ2FXNWpiSFZrWlY5d1lXTnJZV2RsWDJSaGRHRTlWSEoxWlN3S0lDQWdJSHBwY0Y5ellXWmxQVVpoYkhObExBb2dJQ0FnY0hsMGFHOXVYM0psY1hWcGNtVnpQU0krUFRNdU9DSXNDaUFnSUNBaklFNXZJR2x1YzNSaGJHeGZjbVZ4ZFdseVpYTTZJR1YyWlhKNUlHbHRjRzl5ZENBb1pXUjRYMlJxWVc1bmIxOTFkR2xzY3l3Z2IzQmhjWFZsWDJ0bGVYTXNJSFJvWlFvZ0lDQWdJeUJqYjNWeWMyVmZZWEJ3Y3lCaGNIQXBJR2x6SUhCeWIzWnBaR1ZrSUdKNUlHVmtlQzF3YkdGMFptOXliU0JoZENCeWRXNTBhVzFsTGlCRVpXTnNZWEpwYm1jZ2RHaGxiUW9nSUNBZ0l5Qm9aWEpsSUhkdmRXeGtJSEpwYzJzZ2NHbHdJSFJ5ZVdsdVp5QjBieUJ5WlhOdmJIWmxMM3BoSUhWd1ozSmhaR1VnY0d4aGRHWnZjbTB0Y0dsdWJtVmtJR1JsY0hNdUNpQWdJQ0JsYm5SeWVWOXdiMmx1ZEhNOWV3b2dJQ0FnSUNBZ0lDSnZjR1Z1WldSNExtTnZkWEp6WlY5aGNIQWlPaUJiQ2lBZ0lDQWdJQ0FnSUNBZ0lDTWdZWEJ3WDJsa0lEMGdJbWhoYm1SdmRYUnpJaUF0UGlCdFlYUmphR1Z6SUVoaGJtUnZkWFJ6UTI5MWNuTmxRWEJ3TG1Gd2NGOXBaQW9nSUNBZ0lDQWdJQ0FnSUNBaWFHRnVaRzkxZEhNZ1BTQnZjM1F5WDJoaGJtUnZkWFJ6WDJGd2NDNXdiSFZuYVc1ek9raGhibVJ2ZFhSelEyOTFjbk5sUVhCd0lpd0tJQ0FnSUNBZ0lDQmRMQW9nSUNBZ2ZTd0tLUW89JywgJ29zdDJfaGFuZG91dHNfYXBwL19faW5pdF9fLnB5JzogJ0lpSWlUMU5VTWlCRGIzVnljMlVnU0dGdVpHOTFkSE1nWTI5MWNuTmxMV0Z3Y0NCd2JIVm5hVzR1SWlJaUNncGZYM1psY25OcGIyNWZYeUE5SUNJeExqQXVNQ0lLJywgJ29zdDJfaGFuZG91dHNfYXBwL3BsdWdpbnMucHknOiAnSWlJaUNraGhibVJ2ZFhSelEyOTFjbk5sUVhCd0lPS0FsQ0JoSUVOdmRYSnpaU0JCY0hBZ2NHeDFaMmx1SUhSb1lYUWdjM1Z5Wm1GalpYTWdZU0J3WlhJdFkyOTFjbk5sQ2lKRGIzVnljMlVnU0dGdVpHOTFkSE1pSUc5dUwyOW1aaUIwYjJkbmJHVWdiMjRnVTNSMVpHbHZKM01nSWxCaFoyVnpJQ1lnVW1WemIzVnlZMlZ6SWlCd1lXZGxMZ29LU0c5M0lHVnVZV0pzWldRdGMzUmhkR1VnY0dWeWMybHpkSE1LTFMwdExTMHRMUzB0TFMwdExTMHRMUzB0TFMwdExTMHRMUzBLVjJVZ1pHOGdUazlVSUd0bFpYQWdiM1Z5SUc5M2JpQnRiMlJsYkM0Z1ZHaGxJR052ZFhKelpWOWhjSEJ6SUdaeVlXMWxkMjl5YXlCaGJISmxZV1I1SUhCbGNuTnBjM1J6Q21WdVlXSnNaUzlrYVhOaFlteGxJSE4wWVhSbElHWnZjaUJoYmlCaGNIQWdhVzRnWUVOdmRYSnpaVUZ3Y0ZOMFlYUjFjMkFnZDJobGJtVjJaWElnZEdobElHRndjQ0J5WlhCdmNuUnpDblJvWVhRZ2FYUWdaRzlsY3lCdWIzUWdiV0Z1WVdkbElHbDBjeUJ2ZDI0Z2MzUnZjbUZuWlRvS0NpQWdLaUJVYUdVZ1VrVlRWQ0JnVUVGVVEwZ2dMMkZ3YVM5amIzVnljMlZmWVhCd2N5OTJNUzloY0hCekwzdGpiM1Z5YzJWZmFXUjlZQ0JvWVc1a2JHVnlDaUFnSUNBb2MyVjBYMk52ZFhKelpWOWhjSEJmWlc1aFlteGxaQ2tnWTJGc2JITWdZRWhoYm1SdmRYUnpRMjkxY25ObFFYQndMbk5sZEY5bGJtRmliR1ZrS0M0dUxpbGdJR0Z1WkNCMGFHVnVDaUFnSUNCM2NtbDBaWE1nZEdobElISmxkSFZ5Ym1Wa0lIWmhiSFZsSUdsdWRHOGdZRU52ZFhKelpVRndjRk4wWVhSMWMyQXVDaUFnS2lCZ2FYTmZZMjkxY25ObFgyRndjRjlsYm1GaWJHVmtLR052ZFhKelpWOXJaWGtzSUNKb1lXNWtiM1YwY3lJcFlDQnlaV0ZrY3lCZ1EyOTFjbk5sUVhCd1UzUmhkSFZ6WURzS0lDQWdJRzl1YkhrZ2QyaGxiaUJ1YnlCeWIzY2daWGhwYzNSeklIbGxkQ0JrYjJWeklHbDBJR1poYkd3Z1ltRmpheUIwYndvZ0lDQWdZRWhoYm1SdmRYUnpRMjkxY25ObFFYQndMbWx6WDJWdVlXSnNaV1FvTGk0dUtXQWdLRzkxY2lCRVJVWkJWVXhVS1NCaGJtUWdjMlZsWkNCaElISnZkeTRLQ2xOdklHQnBjMTlsYm1GaWJHVmtLQ2xnSUdobGNtVWdhWE1nYW5WemRDQjBhR1VnS21SbFptRjFiSFFxSUdadmNpQmpiM1Z5YzJWeklHNXZZbTlrZVNCb1lYTWdkRzluWjJ4bFpBcDVaWFF1SUZkbElISmxkSFZ5YmlCMGFHVWdjR3hoZEdadmNtMGdaR1ZtWVhWc2RDQW9jMlYwZEdsdVozTXVUMU5VTWw5SVFVNUVUMVZVVTE5RVJVWkJWVXhVWDBWT1FVSk1SVVFzQ21sMGMyVnNaaUJrWldaaGRXeDBhVzVuSUhSdklGUnlkV1VwTENCaGJtUWdZSE5sZEY5bGJtRmliR1ZrS0NsZ0lITnBiWEJzZVNCbFkyaHZaWE1nZEdobElISmxjWFZsYzNSbFpBcDJZV3gxWlNCaVlXTnJJR1p2Y2lCMGFHVWdabkpoYldWM2IzSnJJSFJ2SUhCbGNuTnBjM1F1Q2dwUWJHRjBabTl5YlMxM2FXUmxJR1JsWm1GMWJIUWdUMFpHSUNoemFXNW5iR1VnYTI1dllpa0tMUzB0TFMwdExTMHRMUzB0TFMwdExTMHRMUzB0TFMwdExTMHRMUzB0TFMwdExTMHRMUzB0Q2xSdklHMWhhMlVnYUdGdVpHOTFkSE1nWkdWbVlYVnNkQzFrYVhOaFlteGxaQ0JtYjNJZ1pYWmxjbmtnWTI5MWNuTmxJSFJvWVhRZ2FHRnpJRzVsZG1WeUlHSmxaVzRLZEc5bloyeGxaQ3dnYzJWMElHbHVJRXhOVXl0RFRWTWdjMlYwZEdsdVozTTZDZ29nSUNBZ1QxTlVNbDlJUVU1RVQxVlVVMTlFUlVaQlZVeFVYMFZPUVVKTVJVUWdQU0JHWVd4elpRb0tRMjkxY25ObGN5QjBhR0YwSUdoaGRtVWdZV3h5WldGa2VTQmlaV1Z1SUdWNGNHeHBZMmwwYkhrZ2RHOW5aMnhsWkNCclpXVndJSFJvWldseUlHQkRiM1Z5YzJWQmNIQlRkR0YwZFhOZ0NuWmhiSFZsT3lCdmJteDVJSFZ1ZEc5MVkyaGxaQ0JqYjNWeWMyVnpJR1p2Ykd4dmR5QjBhR2x6SUdSbFptRjFiSFF1SUNoT1QxUkZPaUIwYUdVZ1puSmhiV1YzYjNKcklITmxaV1J6SUdFS1EyOTFjbk5sUVhCd1UzUmhkSFZ6SUhKdmR5QjBhR1VnWm1seWMzUWdkR2x0WlNCaElHTnZkWEp6WlNkeklHRndjQ0J6ZEdGMGRYTWdhWE1nY21WaFpDd2daUzVuTGlCM2FHVnVDbEJoWjJWeklDWWdVbVZ6YjNWeVkyVnpJR2x6SUc5d1pXNWxaQ0J2Y2lCMGFHVWdiR1ZoY201bGNpQm5ZWFJsSUhKMWJuTWdZR2x6WDJOdmRYSnpaVjloY0hCZlpXNWhZbXhsWkdBN0NtOXVZMlVnYzJWbFpHVmtMQ0IwYUdGMElHTnZkWEp6WlNCdWJ5QnNiMjVuWlhJZ1ptOXNiRzkzY3lCMGFHVWdaR1ZtWVhWc2RDNGdVMjhnWm14cGNDQjBhR2x6SUd0dWIySUtLbUpsWm05eVpTb2dZbkp2WVdRZ2RISmhabVpwWXlCcFppQjViM1VnZDJGdWRDQnBkQ0IwYnlCaGNIQnNlU0JsZG1WeWVYZG9aWEpsTENCdmNpQnlkVzRnWVNCdmJtVXRiMlptQ21KaFkydG1hV3hzSU9LQWxDQnpaV1VnVWtWQlJFMUZMaWtLSWlJaUNtWnliMjBnZEhsd2FXNW5JR2x0Y0c5eWRDQkVhV04wTENCUGNIUnBiMjVoYkFvS1puSnZiU0JrYW1GdVoyOHVZMjl1WmlCcGJYQnZjblFnYzJWMGRHbHVaM01LWm5KdmJTQmthbUZ1WjI4dVkyOXVkSEpwWWk1aGRYUm9JR2x0Y0c5eWRDQm5aWFJmZFhObGNsOXRiMlJsYkFwbWNtOXRJR1JxWVc1bmJ5NTFkR2xzY3k1MGNtRnVjMnhoZEdsdmJpQnBiWEJ2Y25RZ1oyVjBkR1Y0ZEY5dWIyOXdJR0Z6SUY4S1puSnZiU0J2Y0dGeGRXVmZhMlY1Y3k1bFpIZ3VhMlY1Y3lCcGJYQnZjblFnUTI5MWNuTmxTMlY1Q2dwbWNtOXRJRzl3Wlc1bFpIZ3VZMjl5WlM1a2FtRnVaMjloY0hCekxtTnZkWEp6WlY5aGNIQnpMbkJzZFdkcGJuTWdhVzF3YjNKMElFTnZkWEp6WlVGd2NBb0tWWE5sY2lBOUlHZGxkRjkxYzJWeVgyMXZaR1ZzS0NrS0NncGtaV1lnWDJSbFptRjFiSFJmWlc1aFlteGxaQ2dwSUMwK0lHSnZiMnc2Q2lBZ0lDQWlJaUpRYkdGMFptOXliUzEzYVdSbElHUmxabUYxYkhRZ1ptOXlJR052ZFhKelpYTWdkMmwwYUNCdWJ5QmxlSEJzYVdOcGRDQkRiM1Z5YzJWQmNIQlRkR0YwZFhNZ2NtOTNMaUlpSWdvZ0lDQWdjbVYwZFhKdUlHSnZiMndvWjJWMFlYUjBjaWh6WlhSMGFXNW5jeXdnSWs5VFZESmZTRUZPUkU5VlZGTmZSRVZHUVZWTVZGOUZUa0ZDVEVWRUlpd2dWSEoxWlNrcENnb0tZMnhoYzNNZ1NHRnVaRzkxZEhORGIzVnljMlZCY0hBb1EyOTFjbk5sUVhCd0tUb0tJQ0FnSUNJaUlnb2dJQ0FnUTI5MWNuTmxJRUZ3Y0NCamIyNW1hV2NnWm05eUlIUm9aU0J3WlhJdFkyOTFjbk5sSUVOdmRYSnpaU0JJWVc1a2IzVjBjeUIwYjJkbmJHVXVDZ29nSUNBZ1RXbHljbTl5Y3lCMGFHVWdjMmx0Y0d4bGMzUWdjM1J2WTJzZ1lYQndJQ2hEWVd4amRXeGhkRzl5UTI5MWNuTmxRWEJ3S1RvZ1lXeDNZWGx6SUdGMllXbHNZV0pzWlN3S0lDQWdJR1Z1WVdKc1pTMXZibXg1SUNodWJ5QmpiMjVtYVdkMWNtRjBhVzl1SUdSeVlYZGxjaWtzSUhOMFlYUmxJR2x1SUVOdmRYSnpaVUZ3Y0ZOMFlYUjFjeTRLSUNBZ0lDSWlJZ29LSUNBZ0lHRndjRjlwWkNBOUlDSm9ZVzVrYjNWMGN5SUtJQ0FnSUc1aGJXVWdQU0JmS0NKRGIzVnljMlVnU0dGdVpHOTFkSE1pS1FvZ0lDQWdaR1Z6WTNKcGNIUnBiMjRnUFNCZktBb2dJQ0FnSUNBZ0lDSlRhRzkzSUc5eUlHaHBaR1VnZEdobElFTnZkWEp6WlNCSVlXNWtiM1YwY3lCelpXTjBhVzl1SUdadmNpQnNaV0Z5Ym1WeWN5QmhibVFnZEdobElDSUtJQ0FnSUNBZ0lDQWlhR0Z1Wkc5MWRITWdaV1JwZEc5eUlHbHVJRk4wZFdScGJ5NGlDaUFnSUNBcENpQWdJQ0JrYjJOMWJXVnVkR0YwYVc5dVgyeHBibXR6T2lCRWFXTjBJRDBnZTMwS0NpQWdJQ0JBWTJ4aGMzTnRaWFJvYjJRS0lDQWdJR1JsWmlCcGMxOWhkbUZwYkdGaWJHVW9ZMnh6TENCamIzVnljMlZmYTJWNU9pQkRiM1Z5YzJWTFpYa3BJQzArSUdKdmIydzZJQ0FqSUhCNWJHbHVkRG9nWkdsellXSnNaVDExYm5WelpXUXRZWEpuZFcxbGJuUUtJQ0FnSUNBZ0lDQWlJaUlLSUNBZ0lDQWdJQ0JJWVc1a2IzVjBjeUJoY21VZ1lTQmpiM0psSUhCaGNuUWdiMllnWlhabGNua2dZMjkxY25ObExDQnpieUIwYUdVZ1lYQndJR2x6SUdGc2QyRjVjd29nSUNBZ0lDQWdJR0YyWVdsc1lXSnNaU0FvYVhRZ1lXeDNZWGx6SUhOb2IzZHpJRzl1SUZCaFoyVnpJQ1lnVW1WemIzVnlZMlZ6S1M0S0lDQWdJQ0FnSUNBaUlpSUtJQ0FnSUNBZ0lDQnlaWFIxY200Z1ZISjFaUW9LSUNBZ0lFQmpiR0Z6YzIxbGRHaHZaQW9nSUNBZ1pHVm1JR2x6WDJWdVlXSnNaV1FvWTJ4ekxDQmpiM1Z5YzJWZmEyVjVPaUJEYjNWeWMyVkxaWGtwSUMwK0lHSnZiMnc2Q2lBZ0lDQWdJQ0FnSWlJaUNpQWdJQ0FnSUNBZ1JXNWhZbXhsWkMxemRHRjBaU0JtYjNJZ2RHaGxJR052ZFhKelpTNEtDaUFnSUNBZ0lDQWdWR2hsSUhOMGIzSmxaQ0JEYjNWeWMyVkJjSEJUZEdGMGRYTWdjbTkzSUdseklIUm9aU0J6YjNWeVkyVWdiMllnZEhKMWRHZzdJRzl1YkhrZ2QyaGxiaUJ1YnlCeWIzY0tJQ0FnSUNBZ0lDQmxlR2x6ZEhNZ1pHOGdkMlVnWm1Gc2JDQmlZV05ySUhSdklIUm9aU0J3YkdGMFptOXliU0JrWldaaGRXeDBDaUFnSUNBZ0lDQWdLRTlUVkRKZlNFRk9SRTlWVkZOZlJFVkdRVlZNVkY5RlRrRkNURVZFTENCa1pXWmhkV3gwSUZSeWRXVXBMZ29LSUNBZ0lDQWdJQ0JYYUhrZ2NtVmhaQ0IwYUdVZ2JXOWtaV3dnYUdWeVpTQW9jbUYwYUdWeUlIUm9ZVzRnYW5WemRDQnlaWFIxY201cGJtY2dkR2hsSUdSbFptRjFiSFFwT2dvZ0lDQWdJQ0FnSUdCMWNHUmhkR1ZmWTI5MWNuTmxYMkZ3Y0hOZmMzUmhkSFZ6WUNCeWRXNXpJRzl1SUVWV1JWSlpJR052ZFhKelpTQndkV0pzYVhOb0lHRnVaQW9nSUNBZ0lDQWdJQ3B2ZG1WeWQzSnBkR1Z6S2lCRGIzVnljMlZCY0hCVGRHRjBkWE1nZDJsMGFDQjNhR0YwWlhabGNpQjBhR2x6SUcxbGRHaHZaQ0J5WlhSMWNtNXpMaUJKWmlCM1pRb2dJQ0FnSUNBZ0lISmxkSFZ5Ym1Wa0lIUm9aU0JrWldaaGRXeDBJSFZ1WTI5dVpHbDBhVzl1WVd4c2VTd2djSFZpYkdsemFHbHVaeUJoSUdOdmRYSnpaU0IzYjNWc1pDQnphV3hsYm5Sc2VRb2dJQ0FnSUNBZ0lISmxMV1Z1WVdKc1pTQm9ZVzVrYjNWMGN5QjBhR0YwSUhOMFlXWm1JR2hoWkNCa2FYTmhZbXhsWkM0Z1VtVmhaR2x1WnlCMGFHVWdjM1J2Y21Wa0lIWmhiSFZsQ2lBZ0lDQWdJQ0FnYldGclpYTWdkR2hoZENCMFlYTnJJR2xrWlcxd2IzUmxiblFnS0dsMElIZHlhWFJsY3lCaVlXTnJJSFJvWlNCellXMWxJSFpoYkhWbEtTd2djMjhnZEdobENpQWdJQ0FnSUNBZ2RHOW5aMnhsSUhOMWNuWnBkbVZ6SUhCMVlteHBjMmhsY3k0S0NpQWdJQ0FnSUNBZ1RtOTBaVG9nZEdocGN5QmtiMlZ6SUU1UFZDQnlaV04xY25ObElIZHBkR2dnWTI5MWNuTmxYMkZ3Y0hNdVlYQnBMbWx6WDJOdmRYSnpaVjloY0hCZlpXNWhZbXhsWkFvZ0lDQWdJQ0FnSUMwdElIUm9ZWFFnYUdWc2NHVnlJRzl1YkhrZ1kyRnNiSE1nZEdocGN5QnRaWFJvYjJRZ2QyaGxiaUJ1YnlCeWIzY2daWGhwYzNSeklDaHBkQ0IwYUdWdUlITmxaV1J6Q2lBZ0lDQWdJQ0FnYjI1bEtTd2dZVzVrSUdobGNtVWdkMlVnY21WaFpDQkRiM1Z5YzJWQmNIQlRkR0YwZFhNZ1pHbHlaV04wYkhrdUNpQWdJQ0FnSUNBZ0lpSWlDaUFnSUNBZ0lDQWdJeUJKYlhCdmNuUmxaQ0JzWVhwcGJIazZJR3RsWlhBZ2RHaGxJRzF2WkhWc1pTQnBiWEJ2Y25SaFlteGxJR0psWm05eVpTQmhjSEF0Y21WbmFYTjBjbmtnY21WaFpIa3VDaUFnSUNBZ0lDQWdabkp2YlNCdmNHVnVaV1I0TG1OdmNtVXVaR3BoYm1kdllYQndjeTVqYjNWeWMyVmZZWEJ3Y3k1dGIyUmxiSE1nYVcxd2IzSjBJRU52ZFhKelpVRndjRk4wWVhSMWN3b2dJQ0FnSUNBZ0lISnZkeUE5SUVOdmRYSnpaVUZ3Y0ZOMFlYUjFjeTV2WW1wbFkzUnpMbVpwYkhSbGNpaGpiM1Z5YzJWZmEyVjVQV052ZFhKelpWOXJaWGtzSUdGd2NGOXBaRDFqYkhNdVlYQndYMmxrS1M1bWFYSnpkQ2dwQ2lBZ0lDQWdJQ0FnYVdZZ2NtOTNJR2x6SUc1dmRDQk9iMjVsT2dvZ0lDQWdJQ0FnSUNBZ0lDQnlaWFIxY200Z1ltOXZiQ2h5YjNjdVpXNWhZbXhsWkNrS0lDQWdJQ0FnSUNCeVpYUjFjbTRnWDJSbFptRjFiSFJmWlc1aFlteGxaQ2dwQ2dvZ0lDQWdRR05zWVhOemJXVjBhRzlrQ2lBZ0lDQmtaV1lnYzJWMFgyVnVZV0pzWldRb1kyeHpMQ0JqYjNWeWMyVmZhMlY1T2lCRGIzVnljMlZMWlhrc0lHVnVZV0pzWldRNklHSnZiMndzSUhWelpYSTZJQ2RWYzJWeUp5a2dMVDRnWW05dmJEb2dJQ01nYm05eFlUb2dSamd5TVFvZ0lDQWdJQ0FnSUNJaUlnb2dJQ0FnSUNBZ0lFVmphRzhnZEdobElISmxjWFZsYzNSbFpDQnpkR0YwZFhNZ1ltRmphenNnZEdobElHTnZkWEp6WlY5aGNIQnpJR1p5WVcxbGQyOXlhd29nSUNBZ0lDQWdJQ2h6WlhSZlkyOTFjbk5sWDJGd2NGOWxibUZpYkdWa0tTQndaWEp6YVhOMGN5QnBkQ0JwYm5SdklFTnZkWEp6WlVGd2NGTjBZWFIxY3k0S0lDQWdJQ0FnSUNBaUlpSUtJQ0FnSUNBZ0lDQnlaWFIxY200Z1ltOXZiQ2hsYm1GaWJHVmtLUW9LSUNBZ0lFQmpiR0Z6YzIxbGRHaHZaQW9nSUNBZ1pHVm1JR2RsZEY5aGJHeHZkMlZrWDI5d1pYSmhkR2x2Ym5Nb1kyeHpMQ0JqYjNWeWMyVmZhMlY1T2lCRGIzVnljMlZMWlhrc0lIVnpaWEk2SUU5d2RHbHZibUZzV3lkVmMyVnlKMTBnUFNCT2IyNWxLU0F0UGlCRWFXTjBXM04wY2l3Z1ltOXZiRjA2SUNBaklHNXZjV0U2SUVVMU1ERXNSamd5TVNCd2VXeHBiblE2SUdScGMyRmliR1U5ZFc1MWMyVmtMV0Z5WjNWdFpXNTBDaUFnSUNBZ0lDQWdJaUlpQ2lBZ0lDQWdJQ0FnVkc5bloyeGxJRzl1YkhrN0lIUm9aWEpsSUdseklHNXZkR2hwYm1jZ2RHOGdZMjl1Wm1sbmRYSmxJR1p2Y2lCb1lXNWtiM1YwY3k0S0lDQWdJQ0FnSUNBaUlpSUtJQ0FnSUNBZ0lDQnlaWFIxY200Z2V3b2dJQ0FnSUNBZ0lDQWdJQ0FpWlc1aFlteGxJam9nVkhKMVpTd0tJQ0FnSUNBZ0lDQWdJQ0FnSW1OdmJtWnBaM1Z5WlNJNklFWmhiSE5sTEFvZ0lDQWdJQ0FnSUgwSycsICdvc3QyX2hhbmRvdXRzX2FwcC9hcGkucHknOiAnSWlJaUNsQjFZbXhwWXlCb1pXeHdaWElnZFhObFpDQmllU0IwYUdVZ1oyRjBhVzVuSUhCaGRHTm9aWE1nS0d4bFlYSnVaWElnWTI5MWNuTmxMV2h2YldVZ0t5QlRkSFZrYVc4Z1ZYQmtZWFJsY3lrS2RHOGdaR1ZqYVdSbElIZG9aWFJvWlhJZ2FHRnVaRzkxZEhNZ1lYSmxJR1Z1WVdKc1pXUWdabTl5SUdFZ1kyOTFjbk5sTGdvS1ZHaHBiaXdnWkdWbVpXNXphWFpsSUhkeVlYQndaWElnWVhKdmRXNWtJSFJvWlNCamIzVnljMlZmWVhCd2N5Qm1jbUZ0WlhkdmNtc25jd3BnYVhOZlkyOTFjbk5sWDJGd2NGOWxibUZpYkdWa1lDNGdTMlZ3ZENCb1pYSmxJSE52SUdKdmRHZ2daMkYwWlNCemFYUmxjeUJwYlhCdmNuUWdZU0J6YVc1bmJHVWdjMjkxY21ObElHOW1DblJ5ZFhSb0lHRnVaQ0J6YnlCbVlXbHNkWEpsY3lCdVpYWmxjaUJpY21WaGF5QjBhR1VnY0dGblpTQjBhR0YwSUdOaGJHeHpJR2wwSUNoaElHaGhibVJ2ZFhSeklIUnZaMmRzWlFwdGRYTjBJRzV2ZENCaVpTQmhZbXhsSUhSdklEVXdNQ0IwYUdVZ1kyOTFjbk5sSUdodmJXVWdiM0lnZEdobElGVndaR0YwWlhNZ2NHRm5aU2t1Q2lJaUlncHBiWEJ2Y25RZ2JHOW5aMmx1WndvS2JHOW5JRDBnYkc5bloybHVaeTVuWlhSTWIyZG5aWElvWDE5dVlXMWxYMThwQ2dwQlVGQmZTVVFnUFNBaWFHRnVaRzkxZEhNaUNnb0taR1ZtSUdselgyaGhibVJ2ZFhSelgyVnVZV0pzWldRb1kyOTFjbk5sWDJ0bGVTa2dMVDRnWW05dmJEb0tJQ0FnSUNJaUlnb2dJQ0FnVW1WMGRYSnVJRlJ5ZFdVZ2FXWWdkR2hsSUVOdmRYSnpaU0JJWVc1a2IzVjBjeUJoY0hBZ2FYTWdaVzVoWW14bFpDQm1iM0lnWUdOdmRYSnpaVjlyWlhsZ0xnb0tJQ0FnSUVaaGFXeHpJRTlRUlU0Z0tISmxkSFZ5Ym5NZ1ZISjFaU2tnYjI0Z1lXNTVJSFZ1Wlhod1pXTjBaV1FnWlhKeWIzSWdjMjhnZEdoaGRDQmhJSEJ5YjJKc1pXMGdhVzRnZEdobENpQWdJQ0JqYjNWeWMyVXRZWEJ3SUd4aGVXVnlJR05oYmlCdVpYWmxjaUJvYVdSbElHaGhibVJ2ZFhSeklIUm9ZWFFnZEdobElHOXdaWEpoZEc5eUlHUnBaQ0J1YjNRZ1kyaHZiM05sQ2lBZ0lDQjBieUJrYVhOaFlteGxMQ0J1YjNJZ1luSmxZV3NnZEdobElHTmhiR3hwYm1jZ2RtbGxkeTRLQ2lBZ0lDQkJjbWR6T2dvZ0lDQWdJQ0FnSUdOdmRYSnpaVjlyWlhrZ0tFTnZkWEp6WlV0bGVTQjhJSE4wY2lrNklIUm9aU0JqYjNWeWMyVWdkRzhnWTJobFkyc3VDaUFnSUNBaUlpSUtJQ0FnSUhSeWVUb0tJQ0FnSUNBZ0lDQWpJRWx0Y0c5eWRHVmtJR3hoZW1sc2VTQnpieUIwYUdseklHMXZaSFZzWlNCcGN5QnBiWEJ2Y25SaFlteGxJR1YyWlc0Z2FXNGdZMjl1ZEdWNGRITWdkMmhsY21VS0lDQWdJQ0FnSUNBaklIUm9aU0JFYW1GdVoyOGdZWEJ3SUhKbFoybHpkSEo1SUdsemJpZDBJSEpsWVdSNUlHRjBJR2x0Y0c5eWRDQjBhVzFsTGdvZ0lDQWdJQ0FnSUdaeWIyMGdiM0JoY1hWbFgydGxlWE11WldSNExtdGxlWE1nYVcxd2IzSjBJRU52ZFhKelpVdGxlUW9nSUNBZ0lDQWdJR1p5YjIwZ2IzQmxibVZrZUM1amIzSmxMbVJxWVc1bmIyRndjSE11WTI5MWNuTmxYMkZ3Y0hNdVlYQnBJR2x0Y0c5eWRDQnBjMTlqYjNWeWMyVmZZWEJ3WDJWdVlXSnNaV1FLQ2lBZ0lDQWdJQ0FnYVdZZ2JtOTBJR2x6YVc1emRHRnVZMlVvWTI5MWNuTmxYMnRsZVN3Z1EyOTFjbk5sUzJWNUtUb0tJQ0FnSUNBZ0lDQWdJQ0FnWTI5MWNuTmxYMnRsZVNBOUlFTnZkWEp6WlV0bGVTNW1jbTl0WDNOMGNtbHVaeWh6ZEhJb1kyOTFjbk5sWDJ0bGVTa3BDaUFnSUNBZ0lDQWdjbVYwZFhKdUlHSnZiMndvYVhOZlkyOTFjbk5sWDJGd2NGOWxibUZpYkdWa0tHTnZkWEp6WlY5clpYa3NJRUZRVUY5SlJDa3BDaUFnSUNCbGVHTmxjSFFnUlhoalpYQjBhVzl1T2lBZ0l5QndlV3hwYm5RNklHUnBjMkZpYkdVOVluSnZZV1F0WlhoalpYQjBDaUFnSUNBZ0lDQWdiRzluTG1WNFkyVndkR2x2YmlnaWIzTjBNbDlvWVc1a2IzVjBjMTloY0hBNklHbHpYMmhoYm1SdmRYUnpYMlZ1WVdKc1pXUWdabUZwYkdWa095QmtaV1poZFd4MGFXNW5JSFJ2SUdWdVlXSnNaV1FpS1FvZ0lDQWdJQ0FnSUhKbGRIVnliaUJVY25WbENnPT0nfQoKaW1wb3J0IGJhc2U2NCwgb3MsIHN1YnByb2Nlc3MsIHN5cwoKREVTVCA9ICIvb3BlbmVkeC9vc3QyX2hhbmRvdXRzX2FwcCIKZm9yIHJlbCwgYjY0IGluIEZJTEVTLml0ZW1zKCk6CiAgICBwYXRoID0gb3MucGF0aC5qb2luKERFU1QsIHJlbCkKICAgIG9zLm1ha2VkaXJzKG9zLnBhdGguZGlybmFtZShwYXRoKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3BlbihwYXRoLCAidyIsIGVuY29kaW5nPSJ1dGYtOCIpIGFzIGZoOgogICAgICAgIGZoLndyaXRlKGJhc2U2NC5iNjRkZWNvZGUoYjY0KS5kZWNvZGUoInV0Zi04IikpCiAgICBwcmludCgib3N0Ml9oYW5kb3V0c19hcHBfaW5zdGFsbDogd3JvdGUgIiArIHBhdGgpCiMgcGlwIGluc3RhbGwgKG5vIGRlcHM7IGFsbCBpbXBvcnRzIHNhdGlzZmllZCBieSBlZHgtcGxhdGZvcm0gYXQgcnVudGltZSkKcmMgPSBzdWJwcm9jZXNzLmNhbGwoW3N5cy5leGVjdXRhYmxlLCAiLW0iLCAicGlwIiwgImluc3RhbGwiLCAiLS1uby1kZXBzIiwgREVTVF0pCmlmIHJjICE9IDA6CiAgICBzeXMuZXhpdChyYykKcHJpbnQoIm9zdDJfaGFuZG91dHNfYXBwX2luc3RhbGw6IHBpcCBpbnN0YWxsZWQgb3N0Mi1oYW5kb3V0cy1hcHAiKQo="

hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-dockerfile-post-python-requirements",
        "# OST2: install the ost2-handouts-app course-app plugin\n"
        "RUN echo " + _B64 + " | base64 -d | python -\n",
    )
)
"""
Tutor plugin: ost2_handouts_default_off

Sets the platform-wide default for the OST2 "Course Handouts" course app to
DISABLED, in both LMS and CMS. Pairs with the ost2_handouts_app package
(HandoutsCourseApp.is_enabled() falls back to OST2_HANDOUTS_DEFAULT_ENABLED
when a course has no stored CourseAppStatus row).

Why a plugin (not a one-off DB change): portable across servers -- copy this
file to a new box, enable, and the default is off there too without having to
remember a command. Existing courses that already have a CourseAppStatus row
are handled by the one-off backfill in ost2_handouts_app/README.md (the knob
only governs courses with no row yet).

This is a settings-only change (openedx-common-settings is bind-mounted), so it
needs `tutor config save` + a restart -- no image rebuild.

Install
-------
    cp ost2_handouts_default_off.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_handouts_default_off
    tutor config save
    tutor local restart lms cms lms-worker cms-worker
"""
from tutor import hooks

__version__ = "1.0.0"

hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-common-settings",
        "# OST2: Course Handouts default OFF platform-wide (never used here).\n"
        "OST2_HANDOUTS_DEFAULT_ENABLED = False\n",
    )
)
"""
Tutor plugin: ost2_handouts_learner_gate

Gates the LEARNER course-home handouts on the per-course "Course Handouts"
toggle (the HandoutsCourseApp from the ost2-handouts-app package). When
handouts are disabled for a course, the course-home API returns
`handouts_html = None`, and the learning MFE's
`outline-tab/widgets/CourseHandouts.jsx` renders nothing for null handouts
(`if (!handoutsHtml) return null;`) -- so no handouts appear in the course-home
sidebar/outline. No learning-MFE rebuild needed.

What it patches (openedx image build, production stage)
-------------------------------------------------------
lms/djangoapps/course_home_api/outline/views.py (OutlineTabView.get):
  * import the helper `is_handouts_enabled` from the installed
    ost2_handouts_app package.
  * both sites that compute `handouts_html` (the enrolled/staff branch and the
    public-outline branch) become:
        handouts_html = get_course_info_section(...) if is_handouts_enabled(course_key) else None

Requires the `ost2-handouts-app` package to be pip-installed in the image
(via the openedx-dockerfile-post-python-requirements stage) so both the
`handouts` course-app entry point and `ost2_handouts_app.api` are importable.

Default ENABLED: a course with no explicit CourseAppStatus row reports enabled
(see HandoutsCourseApp.is_enabled), so behavior is unchanged until staff
disable handouts for a course (or the platform default knob
OST2_HANDOUTS_DEFAULT_ENABLED is set False). `is_handouts_enabled` fails open.

Anchors verified against release/teak @ bfdbcd5de6 (tree-identical to the
deployed image). Each edit is grep-guarded (idempotent) and aborts the build
if its anchor is missing or ambiguous.

Install (enable AFTER ost2-handouts-app is installed in the image)
------------------------------------------------------------------
    cp ost2_handouts_learner_gate.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_handouts_learner_gate
    tutor config save
    tutor images build openedx
    tutor local restart lms lms-worker
"""
import base64

from tutor import hooks

__version__ = "1.0.0"

_VIEWS = "/openedx/edx-platform/lms/djangoapps/course_home_api/outline/views.py"

_EDITS = [
    {
        "path": _VIEWS,
        "desc": "outline views: import is_handouts_enabled",
        "marker": "from ost2_handouts_app.api import is_handouts_enabled",
        "anchor": (
            "from lms.djangoapps.courseware.courses import "
            "get_course_date_blocks, get_course_info_section\n"
        ),
        "replacement": (
            "from lms.djangoapps.courseware.courses import "
            "get_course_date_blocks, get_course_info_section\n"
            "# OST2: per-course Course Handouts toggle gate\n"
            "from ost2_handouts_app.api import is_handouts_enabled\n"
        ),
    },
    {
        "path": _VIEWS,
        "desc": "outline views: gate handouts_html (enrolled/staff branch)",
        # marker is the gated form; unique enough (the suffix never appears unpatched)
        "marker": (
            "            handouts_html = get_course_info_section(request, request.user, course, "
            "'handouts') if is_handouts_enabled(course_key) else None\n"
        ),
        "anchor": (
            "\n            handouts_html = get_course_info_section(request, request.user, course, "
            "'handouts')\n"
        ),
        "replacement": (
            "\n            # OST2: only expose handouts when the Course Handouts app is enabled\n"
            "            handouts_html = get_course_info_section(request, request.user, course, "
            "'handouts') if is_handouts_enabled(course_key) else None\n"
        ),
    },
    {
        "path": _VIEWS,
        "desc": "outline views: gate handouts_html (public-outline branch)",
        "marker": (
            "                handouts_html = get_course_info_section(request, request.user, course, "
            "'handouts') if is_handouts_enabled(course_key) else None\n"
        ),
        "anchor": (
            "\n                handouts_html = get_course_info_section(request, request.user, course, "
            "'handouts')\n"
        ),
        "replacement": (
            "\n                # OST2: only expose handouts when the Course Handouts app is enabled\n"
            "                handouts_html = get_course_info_section(request, request.user, course, "
            "'handouts') if is_handouts_enabled(course_key) else None\n"
        ),
    },
]

_APPLY = r'''
import sys

failed = False
for edit in EDITS:
    with open(edit["path"], encoding="utf-8") as fh:
        src = fh.read()

    if edit["marker"] in src:
        print("ost2_handouts_learner_gate: already applied, skipping: " + edit["desc"])
        continue

    n = src.count(edit["anchor"])
    if n != 1:
        sys.stderr.write(
            "ost2_handouts_learner_gate FATAL: expected exactly 1 anchor for "
            "%r in %s, found %d. The edx-platform source changed (upgraded? "
            "fixed upstream?). Review/remove this Tutor plugin.\n"
            % (edit["desc"], edit["path"], n)
        )
        failed = True
        continue

    with open(edit["path"], "w", encoding="utf-8") as fh:
        fh.write(src.replace(edit["anchor"], edit["replacement"], 1))
    print("ost2_handouts_learner_gate: applied: " + edit["desc"])

if failed:
    sys.exit(1)
'''

PATCH_SCRIPT = "EDITS = " + repr(_EDITS) + "\n" + _APPLY
_B64 = base64.b64encode(PATCH_SCRIPT.encode("utf-8")).decode("ascii")

hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-dockerfile-pre-assets",
        "# OST2: gate learner course-home handouts on the Course Handouts app toggle\n"
        "RUN echo " + _B64 + " | base64 -d | python -\n",
    )
)
"""
Tutor plugin: ost2_handouts_studio_gate (CMS server half)

Stamps an `enabled` flag onto the Studio handouts block info so the
course-authoring MFE can hide the handouts editor on the Updates page when the
per-course "Course Handouts" app is disabled.

How it fits together
--------------------
The authoring MFE Updates page (`src/course-updates/CourseUpdates.jsx`) fetches
the handouts block via `GET /xblock/block-v1:...+type@course_info+block@handouts`
and stores the whole response object in redux (`fetchCourseHandoutsSuccess`).
This patch makes that response include `"enabled": <bool>` *only for the
course_info/handouts block*, computed from the HandoutsCourseApp toggle. The
companion MFE patch (ost2_handouts_studio_gate_authoring) then renders the
handouts editor only when `courseHandouts?.enabled !== false`, so:
  * enabled (or flag absent, e.g. unpatched) -> editor shows (unchanged default)
  * disabled -> editor is hidden.

Why here (get_block_info) and why guarded
-----------------------------------------
`get_block_info` is the single function that builds every xblock GET response
in Studio, including the handouts block. We add the flag right before it
returns, but ONLY when the block is the course_info/handouts block
(`location.block_type == 'course_info' and location.block_id == 'handouts'`),
so no other xblock response or code path is affected and the per-xblock cost is
a couple of attribute comparisons. `is_handouts_enabled` fails open (returns
True) on any error, so a problem in the course-app layer can never hide an
editor the operator did not choose to disable.

Requires the `ost2-handouts-app` package pip-installed in the image (provides
`ost2_handouts_app.api.is_handouts_enabled` and the `handouts` course-app
entry point).

Anchor verified against release/teak @ bfdbcd5de6 (tree-identical to the
deployed image). Grep-guarded + idempotent; fails the build loudly on drift.

Install (after ost2-handouts-app is installed in the image)
-----------------------------------------------------------
    cp ost2_handouts_studio_gate.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_handouts_studio_gate
    tutor config save
    tutor images build openedx
    tutor local restart cms cms-worker
"""
import base64

from tutor import hooks

__version__ = "1.0.0"

_VIEW_HANDLERS = (
    "/openedx/edx-platform/cms/djangoapps/contentstore/"
    "xblock_storage_handlers/view_handlers.py"
)

_EDITS = [
    {
        "path": _VIEW_HANDLERS,
        "desc": "get_block_info: stamp handouts `enabled` flag for course_info/handouts",
        "marker": "# OST2: expose Course Handouts app enabled-state on the handouts block",
        "anchor": (
            "        if include_publishing_info:\n"
            "            add_container_page_publishing_info(xblock, xblock_info)\n"
            "\n"
            "        return xblock_info\n"
        ),
        "replacement": (
            "        if include_publishing_info:\n"
            "            add_container_page_publishing_info(xblock, xblock_info)\n"
            "\n"
            "        # OST2: expose Course Handouts app enabled-state on the handouts block\n"
            "        # so the authoring MFE can hide the handouts editor when the per-course\n"
            "        # Course Handouts app is disabled. Scoped to the course_info/handouts\n"
            "        # block only; fails open (is_handouts_enabled returns True on error).\n"
            "        if (\n"
            "            getattr(xblock.location, 'block_type', None) == 'course_info'\n"
            "            and getattr(xblock.location, 'block_id', None) == 'handouts'\n"
            "        ):\n"
            "            from ost2_handouts_app.api import is_handouts_enabled\n"
            "            xblock_info['enabled'] = is_handouts_enabled(xblock.location.course_key)\n"
            "\n"
            "        return xblock_info\n"
        ),
    },
]

_APPLY = r'''
import sys

failed = False
for edit in EDITS:
    with open(edit["path"], encoding="utf-8") as fh:
        src = fh.read()

    if edit["marker"] in src:
        print("ost2_handouts_studio_gate: already applied, skipping: " + edit["desc"])
        continue

    n = src.count(edit["anchor"])
    if n != 1:
        sys.stderr.write(
            "ost2_handouts_studio_gate FATAL: expected exactly 1 anchor for "
            "%r in %s, found %d. The edx-platform source changed (upgraded? "
            "fixed upstream?). Review/remove this Tutor plugin.\n"
            % (edit["desc"], edit["path"], n)
        )
        failed = True
        continue

    with open(edit["path"], "w", encoding="utf-8") as fh:
        fh.write(src.replace(edit["anchor"], edit["replacement"], 1))
    print("ost2_handouts_studio_gate: applied: " + edit["desc"])

if failed:
    sys.exit(1)
'''

PATCH_SCRIPT = "EDITS = " + repr(_EDITS) + "\n" + _APPLY
_B64 = base64.b64encode(PATCH_SCRIPT.encode("utf-8")).decode("ascii")

hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-dockerfile-pre-assets",
        "# OST2: stamp Course Handouts enabled-state on the Studio handouts block\n"
        "RUN echo " + _B64 + " | base64 -d | python -\n",
    )
)
