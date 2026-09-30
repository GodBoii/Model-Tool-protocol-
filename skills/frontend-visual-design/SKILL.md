---
name: frontend-visual-design
description: Frontend visual design discipline. Use for typography, color systems, surfaces, depth, backgrounds, composition, and visual polish.
---

# Frontend visual design

Make the interface look designed for this product, not generated from a default template.

## Foundation

- Define 4-6 named color tokens before styling broad surfaces: background, surface, text, muted text, border, accent, and danger/success only when needed.
- Use one clear type system: display, body, utility/data. Avoid default browser fonts and avoid using one generic family for everything when visual identity matters.
- Match density to product type. Operational tools need tighter spacing and clear tables. Editorial or marketing pages can use larger whitespace and stronger type.
- Use sentence case for UI text unless the brand system says otherwise.
- Keep radius consistent by level: tighter for inputs and controls, slightly larger for cards or panels, largest for media only when it fits the brand.
- Use depth intentionally: elevation, overlap, shadows, texture, blur, and layering should explain hierarchy or focus.

## Color and surfaces

- Avoid one-note palettes where every surface is the same hue family.
- Avoid default AI looks: purple-blue gradients, beige editorial clones, floating blobs, glass cards everywhere, and random neon on dark backgrounds.
- Use contrast first. Accent color should guide action, not decorate every heading.
- Shadows should match the environment. Pure black shadows on tinted surfaces often look pasted on.
- Background patterns, aurora, matrix, noise, mesh, grain, and blobs must serve the subject or state. Do not add them as filler.

## Typography

- Large headlines need wide containers and controlled line-height. Do not allow six-line hero headings.
- Body text should stay readable, usually around 45-75 characters per line.
- Use tabular numbers for finance, analytics, counters, timers, and dashboards.
- Avoid orphaned single words in important headings when CSS support exists.

## Self-audit

- Could this exact screen fit another random SaaS app? If yes, add subject-specific structure, copy, data, imagery, or interaction.
- Is there one clear visual signature? If there are five, remove three.
- Does the polish survive mobile, long text, real data, empty states, and dark/light themes?
