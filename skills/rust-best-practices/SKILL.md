---
name: rust-best-practices
description: Rust best practices. Use when reading, editing, writing, or reviewing Rust code.
---

# Rust best practices

Write Rust that uses the type system to encode ownership, validity, and failure clearly.

## Rules

- Model states and variants with enums instead of loose structs plus flags.
- Prefer borrowing over cloning. Clone only when ownership transfer or lifetime simplification is worth the cost.
- Use `Result` for recoverable failure and `Option` for absence. Avoid `unwrap` and `expect` in library or shipped paths unless the invariant is proven and the message explains it.
- Keep parsing and validation at boundaries. Convert raw input into domain types early.
- Use newtypes for IDs, units, and values that should not be mixed.
- Prefer iterator clarity over clever chains that hide control flow.
- Keep error types useful. Add context at boundaries and preserve causes.
- Avoid shared mutable state. Reach for ownership first, then synchronization only when needed.
- Write tests for domain behavior and edge cases. Use property tests when invariants matter.
- Run `cargo fmt`, `cargo clippy`, and tests when available.
