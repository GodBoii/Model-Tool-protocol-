---
name: go-best-practices
description: Go best practices. Use when reading, editing, writing, or reviewing Go code.
---

# Go best practices

Write Go that is simple, explicit, concurrent only when needed, and easy to operate.

## Rules

- Keep packages small and named by responsibility, not vague layers.
- Accept interfaces, return concrete types, and define interfaces near consumers.
- Handle errors explicitly with context. Do not ignore errors with `_` unless the call is truly safe to ignore.
- Use `context.Context` for cancellation, deadlines, and request-scoped values. Pass it first when used.
- Keep goroutine lifetimes clear. Avoid starting goroutines without cancellation, ownership, and error handling.
- Prefer table-driven tests for input/output behavior and edge cases.
- Avoid global mutable state. Inject clocks, clients, loggers, and stores where tests need control.
- Use the race detector for concurrent code when practical.
- Keep JSON, SQL, HTTP, and env parsing at boundaries. Validate before passing typed data inward.
- Do not over-abstract. Go code should usually be direct and readable.
