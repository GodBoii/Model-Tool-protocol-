---
name: java-best-practices
description: Java best practices. Use when reading, editing, writing, or reviewing Java code.
---

# Java best practices

Write Java that keeps domain rules explicit, controls nulls, and uses the platform without ceremony for its own sake.

## Rules

- Keep nullability deliberate. Prefer non-null by default, `Optional` for return absence, and validation at boundaries.
- Use records, enums, sealed classes, and value objects when they model the domain more clearly than mutable beans.
- Keep constructors and factories responsible for valid objects. Avoid half-initialized objects.
- Prefer dependency injection over hidden construction of clients, clocks, repositories, and external services.
- Handle exceptions at the level that can add context or recover. Do not catch and ignore.
- Keep transactions, network calls, and file I/O out of pure domain logic.
- Avoid overusing inheritance. Prefer composition unless polymorphism is the real model.
- Keep public interfaces small and stable. Do not expose internal collections as mutable state.
- Use streams where they improve clarity. Use loops when they are clearer.
- Test behavior with real collaborators where practical. Mock external systems, not your own domain logic.
