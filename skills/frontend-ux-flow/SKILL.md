---
name: frontend-ux-flow
description: Frontend UX flow and product behavior. Use for navigation, workflows, information hierarchy, forms, onboarding, dashboards, and feature behavior.
---

# Frontend UX flow

Design the product behavior before decorating the screen.

## Rules

- Identify the user's primary task and make that task reachable without reading instructions.
- Put the most useful control near the data or object it acts on.
- Use progressive disclosure: show common actions directly, tuck rare or dangerous actions into menus, drawers, or confirmation flows.
- Navigation should answer where am I, where can I go, what changed, and how do I get back.
- Dashboards should optimize scanning, comparison, filtering, and repeated action. Avoid marketing-style hero layouts in operational tools.
- Forms should group related inputs, validate early enough to help, and show errors beside the field they affect.
- Empty states should explain what is missing and give the next useful action.
- Error states should say what failed and what the user can do. Avoid vague apology copy.
- Loading states should preserve layout shape when possible. Use skeletons for known content, progress for measurable work, and semantic activity indicators for AI work.
- Destructive actions need clear labels, scoped confirmation, and undo when possible.
- Keep feature behavior real. Buttons should do something, route somewhere, or be visibly disabled with a reason.
- Preserve keyboard, touch, and screen-reader workflows while improving visuals.

## Avoid

- In-app text explaining the UI itself when the interaction should be self-evident.
- Dead-end pages with no back path.
- Multiple primary actions fighting for attention.
- Modals for simple inline edits.
- Fake feature cards that describe functionality the app does not implement.
