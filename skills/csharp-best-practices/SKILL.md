---
name: csharp-best-practices
description: C# best practices. Use when reading, editing, writing, or reviewing C# and .NET code.
---

# C# best practices

Write C# that uses strong types, clear async flow, and .NET conventions without hiding business rules in framework glue.

## Rules

- Enable and respect nullable reference types. Treat warnings as design feedback.
- Use records, enums, and value objects for domain data that should be immutable or constrained.
- Keep validation at boundaries: controllers, message handlers, config loading, files, and database reads.
- Use dependency injection for clients, clocks, repositories, loggers, and services with external effects.
- Await async work. Avoid `.Result`, `.Wait()`, and fire-and-forget tasks unless explicitly owned and observed.
- Pass `CancellationToken` through I/O and request-scoped async paths.
- Keep EF, HTTP, serialization, and framework concerns away from core domain logic.
- Do not catch broad exceptions unless adding context, translating failure, logging, or recovering.
- Prefer clear LINQ over clever LINQ. Use loops when they explain the work better.
- Test domain behavior without a web host when possible. Use integration tests for framework wiring and database behavior.
