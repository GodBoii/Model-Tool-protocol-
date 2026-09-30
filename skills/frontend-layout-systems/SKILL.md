---
name: frontend-layout-systems
description: Frontend layout systems. Use for responsive grids, dashboards, navigation shells, file trees, graphs, panels, cards, and page structure.
---

# Frontend layout systems

Build layouts with stable structure, predictable responsive behavior, and no accidental overlap.

## Rules

- Start with the shell: navigation, main region, side panels, command areas, and persistent status surfaces.
- Use CSS Grid for two-dimensional page structure and dense card systems. Use flex for one-dimensional alignment.
- Define containers with `max-width`, `minmax()`, `clamp()`, `aspect-ratio`, and `min-height` instead of fragile fixed sizes.
- Prefer `min-height: 100dvh` over `height: 100vh` for full viewport sections.
- Reserve cards for repeated items, framed tools, dialogs, and content groups that need a boundary. Do not put cards inside cards.
- Data-heavy pages should prioritize alignment, column stability, sticky headers where useful, and readable empty/filter states.
- File trees need indentation, icons, selected/focused state, disclosure controls, and keyboard navigation.
- Graphs need labeled axes, legends, units, tooltip behavior, empty states, and enough contrast. Do not use unlabeled decorative charts.
- Keep nav active state visible. Docks, sidebars, tabs, and breadcrumbs should show location.
- Plan for long names, localization, dynamic counts, missing images, and permission-based hidden actions.
- Check wide, desktop, tablet, and mobile layouts. Do not let hover labels, badges, or dynamic text resize fixed-format controls.

## Avoid

- Three identical feature cards by default.
- Centering every section regardless of content.
- Arbitrary `z-index: 9999`.
- Horizontal overflow caused by offscreen animations or absolute-positioned decoration.
- Layouts that depend on ideal text length or perfect image aspect ratios.
