---
name: frontend-accessibility-performance
description: Frontend accessibility and performance. Use for keyboard UX, contrast, semantic HTML, reduced motion, responsive media, Core Web Vitals, and production readiness.
---

# Frontend accessibility and performance

Accessibility and performance are part of the feature, not cleanup.

## Accessibility

- Use semantic HTML first. Add ARIA only to fill gaps, not to replace native behavior.
- Every interactive element must be keyboard reachable, visibly focused, and operable without a mouse.
- Text contrast should meet WCAG AA: 4.5:1 for normal text and 3:1 for large text or non-text UI indicators.
- Touch targets should be at least 44px where practical.
- Inputs need labels, errors tied with `aria-describedby`, and meaningful autocomplete.
- Images need purposeful alt text. Decorative images should be hidden from assistive tech.
- Dialogs need focus trap, escape close, labelled title, and focus return.
- Menus, tabs, accordions, comboboxes, and sliders need correct keyboard behavior.
- Support reduced motion, high contrast preference when relevant, and system color scheme unless the product has a fixed brand theme.
- Do not use color alone to communicate status.

## Performance

- Keep layout stable. Set image dimensions or aspect ratios to avoid CLS.
- Lazy-load non-critical images and media. Preload only critical assets.
- Use responsive images and modern formats when the stack supports them.
- Avoid shipping large animation libraries for small CSS transitions.
- Keep continuous effects bounded, paused offscreen, and disabled on low-capability inputs when appropriate.
- Avoid expensive filters, giant blurs, and many box shadows on scrolling surfaces.
- Virtualize long lists and tables when row count is large.
- Debounce or throttle high-frequency input, scroll, resize, and pointer handlers.
- Clean up event listeners, intervals, observers, animation frames, and ScrollTriggers.
- Check console errors, network failures, hydration issues, layout shift, and bundle impact when changing frontend behavior.
