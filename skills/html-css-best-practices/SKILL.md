---
name: html-css-best-practices
description: HTML and CSS best practices. Use when reading, editing, writing, or reviewing markup, styles, layouts, or accessibility.
---

# HTML and CSS best practices

Build interfaces that are semantic, accessible, responsive, and maintainable.

## HTML

- Use native semantic elements first: `button`, `a`, `form`, `label`, `input`, `main`, `nav`, `section`, `article`, `header`, `footer`.
- Use a real `button` for actions and a real `a` for navigation. Do not fake either with clickable `div`s.
- Keep heading order logical. Do not skip levels for visual size.
- Every form input needs a label or accessible name. Icons alone need accessible labels.
- Use alt text that describes the image's purpose. Decorative images should be ignored by assistive tech.
- Keep interactive targets keyboard reachable and visible when focused.
- Do not put interactive elements inside other interactive elements.

## CSS

- Prefer layout primitives: flex, grid, container queries, aspect ratio, min/max constraints, and logical properties.
- Avoid fragile fixed heights unless the UI is truly fixed-format.
- Use a small design token set for spacing, colors, typography, radius, shadows, and z-index.
- Keep specificity low. Avoid `!important` unless overriding third-party CSS with no better hook.
- Make text fit at mobile and desktop sizes. Do not rely on viewport-scaled font sizes.
- Respect reduced motion. Do not animate layout-heavy properties when transform or opacity will do.
- Test real responsive states, overflow, focus, hover, disabled, loading, empty, and error states.
- Keep CSS tied to components or clear layers. Do not let global styles become hidden dependencies.
