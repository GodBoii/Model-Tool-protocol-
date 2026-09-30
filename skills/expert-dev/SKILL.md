---
name: expert-dev
description: Expert developer coding discipline. Use at the start of Codex chats and before planning, writing, editing, or reviewing code.
---

# Expert developer discipline

Act like an expert engineer: solve the user's actual problem, keep the codebase easier to change, and make incorrect usage harder to write.

## First moves

- Read the relevant code before deciding. Let the existing architecture, tests, naming, and framework choices guide the change.
- Keep scope tight. Do not refactor unrelated code because it looks imperfect.
- Prefer deleting or simplifying code over adding new layers.
- Choose boring, explicit code unless a stronger abstraction clearly reduces real complexity.

## Expert code habits

- Make illegal states impossible where the language allows it.
- Validate data at boundaries: user input, APIs, files, environment, databases, queues, browser storage, and third-party SDKs.
- Keep domain logic away from framework glue, UI rendering, controllers, route handlers, CLI parsing, and persistence details.
- Design small interfaces with real behavior behind them. Avoid shallow wrappers that only rename another call.
- Keep dependencies flowing inward. Pass clients, clocks, randomness, storage, and external services into code that needs them.
- Handle errors deliberately. Include context that helps debug without leaking secrets.
- Test behavior through public interfaces. Mock only what cannot reasonably run locally.
- Add observability where the feature will fail in production: structured logs, metrics, traces, or clear diagnostics.
- Optimize after measuring. Do not trade clarity for guessed performance.
- Respect the repo's tools: formatter, linter, type checker, test runner, build system, and existing conventions.

## Blast-radius discipline

Expert code protects existing behavior, not only the new path.

- Before changing code, identify what the change could break outside the edited files.
- Look past simple caller lists. Check persisted data, wire formats, feature flags, generated types, framework lifecycle, background jobs, scheduled tasks, and code that consumes the same data in another language.
- Find the one or two safety facts the change depends on. Example: "this function only receives validated orders" or "this migration is backward-compatible with the old reader."
- Prove important safety facts with real code when cheap: run the relevant test, write a focused repro script, call the real function, or reproduce it in the running app.
- If a safety fact cannot be proven cheaply, say it is unproven. Do not present a confident writeup based only on reasoning.
- Report real risks with file paths, likelihood, impact, and the cheapest check that would catch the failure.
- Separate confirmed risks from cleared risks. A search that finds no callers is useful only when the searched symbol is the real integration point.

## Things to avoid

- Giant functions or files that mix validation, business rules, persistence, UI, and side effects.
- Premature abstractions, generic managers, catch-all utility files, and pattern cargo culting.
- Boolean state soup when a finite state model would be clearer.
- Silent failure, swallowed exceptions, broad catches, and fallback behavior nobody asked for.
- Global mutable state unless the runtime or framework requires it.
- Hidden I/O inside code that looks pure.
- Duplicated business rules across client, server, scripts, and tests.
- Casual casts, unchecked dynamic data, and comments that merely restate fragile invariants.
- Stray debug output in shipped code.
- Changes that only work for the happy path while quietly breaking existing users, stored data, old clients, or downstream jobs.

## Language skill routing

Before coding in a language or stack, read the matching skill:

- Python: `C:/Users/prajw/Downloads/Trader/skills/python-best-practices/SKILL.md`
- HTML or CSS: `C:/Users/prajw/Downloads/Trader/skills/html-css-best-practices/SKILL.md`
- JavaScript: `C:/Users/prajw/Downloads/Trader/skills/javascript-best-practices/SKILL.md`
- TypeScript: `C:/Users/prajw/Downloads/Trader/skills/typescript-best-practices/SKILL.md`
- React, JSX, or TSX: `C:/Users/prajw/Downloads/Trader/skills/react-tsx-best-practices/SKILL.md`
- Frontend UI, UX, layout, motion, accessibility, visual design, or browser verification: `C:/Users/prajw/Downloads/Trader/skills/frontend-expert/SKILL.md`
- SQL, migrations, or database queries: `C:/Users/prajw/Downloads/Trader/skills/sql-best-practices/SKILL.md`
- Shell, Bash, PowerShell, CI scripts, or command automation: `C:/Users/prajw/Downloads/Trader/skills/shell-best-practices/SKILL.md`
- Go: `C:/Users/prajw/Downloads/Trader/skills/go-best-practices/SKILL.md`
- Rust: `C:/Users/prajw/Downloads/Trader/skills/rust-best-practices/SKILL.md`
- Java: `C:/Users/prajw/Downloads/Trader/skills/java-best-practices/SKILL.md`
- C# or .NET: `C:/Users/prajw/Downloads/Trader/skills/csharp-best-practices/SKILL.md`

If multiple languages are involved, read each relevant skill. Apply the narrower skill over the general one when they overlap.
