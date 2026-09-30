---
name: frontend-verification
description: Frontend browser verification. Use before finishing UI work that changes layout, styling, animation, routing, components, or user interaction.
---

# Frontend verification

Verify frontend work in the browser whenever the change can be seen or clicked.

## What to check

- Start the app with the repo's dev command when needed. If the port is taken, use another port and report the URL.
- Use screenshots for visual changes. Check at least desktop and mobile; add tablet and wide viewports for layout-heavy pages.
- Inspect console errors, failed network requests, hydration warnings, and runtime exceptions.
- Exercise the changed interaction: click, keyboard, focus, hover when available, form submit, menu close, route change, loading, empty, and error paths.
- Check that text does not overflow buttons, cards, nav, tables, inputs, badges, or fixed-format controls.
- Confirm no incoherent overlap, accidental horizontal scroll, clipped focus rings, hidden controls, or broken sticky/pinned sections.
- Verify reduced motion behavior for meaningful animation changes.
- For canvas, WebGL, charts, maps, or 3D scenes, check that pixels actually render and are not blank.
- For asset changes, verify images, icons, favicon, and fonts load correctly.
- For blast-radius safety, test nearby pages or components that reuse the edited styling, component, hook, or token.

## Output

- Tell the user what was verified and what could not be verified.
- Include the local URL when a dev server is running.
- Keep screenshots or artifacts in the repo only when they are requested or already part of the workflow.
