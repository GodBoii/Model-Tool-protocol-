---
name: react-tsx-best-practices
description: React and TSX best practices. Use when reading, editing, writing, or reviewing React components, hooks, JSX, or TSX files.
---

# React and TSX best practices

Build React code that keeps UI state explicit, separates domain logic from rendering, and treats accessibility as part of the component contract.

## Routing

Also read `C:/Users/prajw/Downloads/Trader/skills/typescript-best-practices/SKILL.md` for `.tsx` work.

## Rules

- Keep components focused on rendering and interaction. Move business rules, parsing, formatting, and data transforms into plain TypeScript modules.
- Model UI state with discriminated unions when states are mutually exclusive. Avoid boolean soup like `isLoading`, `hasError`, `isEmpty`, `isReady`.
- Keep side effects in the right place. Use effects for synchronization with external systems, not for deriving render data.
- Derive values during render when possible. Do not mirror props into state unless there is a real editing or reset requirement.
- Use stable keys from data, not array indexes, when items can reorder, insert, or delete.
- Make every interactive control accessible by keyboard and screen reader. Use native controls before ARIA.
- Handle loading, empty, error, disabled, optimistic, and success states without layout jumps.
- Avoid giant components. Extract when it improves naming, reuse, or testability, not just to make files smaller.
- Keep server/client boundaries explicit in frameworks like Next.js. Do not move secrets or server-only work into client components.
- Use framework data loading and mutation primitives where the project already has a pattern.
- Test user behavior with real rendering tools. Do not test private hook internals when the component behavior is observable.
