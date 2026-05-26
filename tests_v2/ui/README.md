# tests_v2/ui — Clickability Sentinel

A focused test that catches the bug class **"control is rendered but a user can't actually click it because something is on top of it"** — z-index inversions, modal backdrops left visible, sticky overlays at the wrong layer, `pointer-events:none` on a parent, etc.

For every page in the test list, at every viewport in the test list, it:

1. Loads the page in headless Chromium with an authenticated session.
2. Finds every interactive control (`button`, `input`, `select`, `textarea`, `a[href]`, `[role="button"|"link"|"tab"|"menuitem"|"checkbox"|"radio"|"switch"]`).
3. Skips controls that are intentionally not currently visible (`display:none`, `visibility:hidden`, `opacity:0`, off-viewport, `aria-hidden="true"`, `pointer-events:none`, or inside an ancestor that is any of the above).
4. For each remaining control, calls `document.elementFromPoint(center.x, center.y)` and asserts the returned element is either the control, a descendant, or an ancestor (any of those means the user's click would actually hit the control).
5. If something else is returned, the control is reported as **blocked**, with the blocker's selector, z-index, position style, and visible text — enough to fix the CSS without re-reproducing.

## Running

```
# All pages × all viewports (default = 4 viewports × 14 pages = 56 cases, ~3–5 min)
C:\Users\james\miniconda3\envs\aihub2.1\python.exe -m pytest tests_v2/ui/ -v --tb=short

# One viewport
C:\Users\james\miniconda3\envs\aihub2.1\python.exe -m pytest tests_v2/ui/ -v -k desktop_1920

# One page
C:\Users\james\miniconda3\envs\aihub2.1\python.exe -m pytest tests_v2/ui/ -v -k _workflow_tool

# Watch the browser (headed) — uncomment headless=False in conftest.py first
```

The suite auto-skips itself with a clear message if the main app (5001) or CC (5091) aren't responding.

## Output when something is blocked

```
FAIL tests_v2/ui/test_control_reachability.py::test_controls_are_reachable[desktop_1920-_workflow_tool]

  2 unreachable control(s) on /workflow_tool @ 1920×1080:
    • input#workflow-name-field.form-control  text='Workflow name'
        rect={'x': 240, 'y': 312, 'w': 280, 'h': 36}  probe=(380,330)  z=auto pos=relative
        BLOCKED BY: div.modal-backdrop.show  z=1040  pos=fixed  text=''
    • button#save-workflow.btn.btn-primary  text='Save'
        rect={'x': 840, 'y': 720, 'w': 90, 'h': 32}  probe=(885,736)  z=auto pos=static
        BLOCKED BY: div.toast-container  z=9999  pos=fixed  text='Saved 3 minutes ago'
```

That tells you: on `/workflow_tool` at 1920×1080, two controls are blocked — the workflow name input and the save button — and the offenders are the modal backdrop (likely left visible after a modal close) and a toast container that's too far up the z-stack.

## What it intentionally does NOT catch

- Controls that are below-the-fold and require scrolling (those are skipped — scrolling is normal user behavior).
- Controls that look weird but ARE clickable (visual regressions need screenshot diffing — Tier 2 of the testing plan).
- Functional bugs ("click does nothing"). Reachability tests that the click would land on the control; not that the handler does the right thing.

## Adding more pages

Edit `MAIN_PAGES` or `CC_PAGES` in `test_control_reachability.py`. New paths show up as new parametrized cases automatically — no other changes needed.

## Adding more viewports

Edit `VIEWPORTS` in the same file. The tuple is `(label, width, height)`.

## Auth

The suite logs in once per session as `admin / admin` (configurable via `UI_TEST_USER` / `UI_TEST_PASS` env vars) and reuses the storage state across every test. CC service routes (`/classic`, `/ops`) are hit without main-app auth — CC's `/api/ops/*` is currently unauthenticated by design (see the bug list in `tests_v2/COVERAGE.md`).
