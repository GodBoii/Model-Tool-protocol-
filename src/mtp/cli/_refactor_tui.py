"""Refactor tui.py: remove code blocks now living in tui_theme, tui_toast, tui_completers."""
import sys

filepath = r'src\mtp\cli\tui.py'
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"Original: {len(lines)} lines")

# Ranges to REMOVE (1-indexed, inclusive)
remove_ranges = [
    (79, 279),    # Theme: console setup, unicode detect, symbols, colors, drawing primitives
    (2075, 2258), # Toast notifications + @-file / /command / merged completers
    (2279, 2325), # _build_prompt_prefix_html + _build_bottom_toolbar (kept in tui_completers)
]

# Range to REPLACE (1-indexed, inclusive)
# PromptSession setup block → single call to _build_prompt_session
replace_start, replace_end = 2398, 2449
replacement_lines = [
    "    # \u2500\u2500 Build prompt session (prompt_toolkit or fallback) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n",
    "    ptk_session = _build_prompt_session(state, lambda: _print_banner(state))\n",
    "\n",
]

# Build set of 0-indexed lines to remove
remove_set = set()
for start, end in remove_ranges:
    for i in range(start - 1, end):
        remove_set.add(i)

# Build output
output = []
for i, line in enumerate(lines):
    if i in remove_set:
        continue
    # Handle replace range
    if replace_start - 1 <= i < replace_end:
        if i == replace_start - 1:
            output.extend(replacement_lines)
        continue
    output.append(line)

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(output)

print(f"New: {len(output)} lines")
print(f"Removed {len(lines) - len(output)} lines (net)")
