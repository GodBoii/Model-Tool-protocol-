---
name: frontend-motion-interactions
description: Frontend motion and interaction design. Use for animations, transitions, hover effects, text reveals, image trails, cursor effects, tilts, gooey effects, snaps, pinned scroll, and background transitions.
---

# Frontend motion and interactions

Motion should explain state, guide attention, or create a deliberate product signature. It should not be scattered decoration.

## Decision rules

- Choose one motion role: feedback, continuity, hierarchy, delight, progress, or storytelling.
- Prefer CSS transitions for simple hover, focus, disclosure, and state swaps.
- Use GSAP for complex timelines, pinned scroll, scrubbed scroll, SVG draw, and staged orchestration.
- Use Framer Motion or Motion for React layout transitions, component entrances, shared layout, and gesture-driven interaction when already in the stack.
- Use canvas/WebGL only when the effect truly needs pixels, particles, shaders, trails, image distortion, or 3D.
- Read `C:/Users/prajw/Downloads/Trader/skills/ui-motion-recipes/SKILL.md` for production-ready recipes before building dropdowns, modals, panels, tabs, tooltips, accordions, counters, toggles, border beams, image reveals, or AI activity indicators.

## Motion quality

- Animate `transform` and `opacity` first. Avoid animating layout-heavy properties like `top`, `left`, `width`, `height`, and `margin`.
- Match duration to distance and importance. Tiny hover feedback should be fast; page-level transitions can be slower.
- Use asymmetric entrance and exit when it feels natural: enter with more presence, exit faster.
- Keep hover-in and hover-out intentional. Hover-out should usually settle faster than hover-in.
- Use stagger for grouped content, but keep total stagger short enough that the interface does not feel blocked.
- Text reveals should preserve readability and not trap content behind scroll tricks.
- Cursor effects, magnetic buttons, image trails, tilts, gooey filters, docks, snaps, wheel interactions, matrix effects, and ambient backgrounds should be opt-in signature moments, not defaults.
- Pinned and scroll-scrubbed sections need clear start/end ranges and must not hijack ordinary reading.
- Background transitions should not reduce text contrast or make content harder to scan.

## Required safeguards

- Respect `prefers-reduced-motion`.
- Disable heavy hover or cursor effects on coarse pointers and touch devices.
- Pause or throttle continuous animations when offscreen.
- Remove development markers and debug overlays before finishing.
- Verify motion in a real browser when it affects layout, scroll, or interaction.
