---
name: frontend-components-states
description: Frontend component and state design. Use for buttons, cards, inputs, menus, dropdowns, tabs, sliders, switches, lists, tables, docks, and reusable UI.
---

# Frontend components and states

Components should make state visible, preserve layout, and use familiar controls for familiar jobs.

## Controls

- Use icon buttons for common tool actions when a standard icon exists, with accessible labels and tooltips for ambiguity.
- Use text buttons for clear commands. Use icon + text when the icon improves scanning.
- Use toggles or checkboxes for binary settings, segmented controls for modes, sliders or steppers for numeric adjustment, menus for option sets, and tabs for peer views.
- Use native controls where possible. Reach for ARIA only when native HTML cannot represent the interaction.
- Disabled controls should explain why when the reason is not obvious.

## States

- Every interactive component needs default, hover, focus-visible, active/pressed, disabled, loading, selected/current, error, and success states where applicable.
- Inputs need labels, validation text, helper text when useful, autocomplete where appropriate, and error association.
- Dropdowns and menus need keyboard navigation, escape close, outside click close, focus return, and viewport collision handling.
- Tabs need stable panels, selected state, keyboard movement, and no layout jump.
- Sliders need visible value, min/max/step, keyboard support, and direct input when precision matters.
- Cards should expose the clickable target clearly. Avoid making the whole card clickable when it contains nested controls.
- Lists need empty, filtered-empty, loading, reorder, selection, and bulk-action states when the workflow supports them.
- Tables need sorting, filtering, pagination or virtualization, sticky headers when useful, and clear row actions.

## Logic

- Model complex UI state explicitly. Use finite states instead of many booleans when states exclude each other.
- Keep component logic close when it only serves the component. Move domain rules and cross-component behavior into plain modules.
- Avoid animation or styling that hides delayed or failed state changes.
