---
name: javascript-best-practices
description: JavaScript best practices. Use when reading, editing, writing, or reviewing JavaScript code.
---

# JavaScript best practices

Write JavaScript with explicit data flow, clear async behavior, and minimal surprise.

## Rules

- Prefer `const`; use `let` only when reassignment is needed. Avoid `var`.
- Validate external data before using it: JSON, URL params, form data, storage, SDK responses, and API responses.
- Avoid implicit coercion. Use strict equality and explicit parsing for numbers, booleans, and dates.
- Keep async code readable. Await promises, handle failures, and avoid floating promises unless intentionally fire-and-forget.
- Do not mix callbacks, promises, and async/await in the same path unless an API forces it.
- Keep pure logic separate from DOM, network, storage, timers, and process state.
- Avoid shared mutable globals. Pass dependencies as arguments when behavior needs to vary in tests or environments.
- Prefer small modules with clear exports. Avoid dumping unrelated helpers into one utility file.
- Use `AbortController`, cleanup functions, or lifecycle hooks for cancellable work.
- Use structured logging in shipped code. Avoid stray `console.log`.
- Prefer real tests over heavy mocking. Test behavior, edge cases, and failure paths.
- If the project supports TypeScript, prefer TypeScript for new shared or complex modules.
