#!/usr/bin/env python3
"""
Demonstration of the Session Title Auto-Generation Feature

This script shows how the new feature works in the MTP TUI.
"""

import re


def _generate_session_title_from_prompt(prompt: str, max_words: int = 4, max_chars: int = 50) -> str:
    """Generate a session title from the first user prompt."""
    cleaned = re.sub(r'@[^\s]+', '', prompt)
    cleaned = ' '.join(cleaned.split())
    words = cleaned.split()[:max_words]
    title = ' '.join(words)
    if len(title) > max_chars:
        title = title[:max_chars].rsplit(' ', 1)[0]
        if title:
            title += '...'
    if len(title.strip()) < 3:
        return "Quick chat"
    return title.strip()


def demo():
    """Demonstrate the session title generation feature."""
    
    print("=" * 80)
    print("MTP TUI - SESSION TITLE AUTO-GENERATION FEATURE DEMO")
    print("=" * 80)
    print()
    
    print("PROBLEM:")
    print("  Before this feature, all sessions showed cryptic IDs like:")
    print("    • 6db601d1d4 (unnamed)")
    print("    • a4ec81e (unnamed)")
    print("    • b1d90e1a (unnamed)")
    print()
    print("  Users couldn't tell which session contained which conversation!")
    print()
    
    print("SOLUTION:")
    print("  Now, session titles are automatically generated from the first")
    print("  user message, making sessions instantly recognizable.")
    print()
    
    print("=" * 80)
    print("EXAMPLES")
    print("=" * 80)
    print()
    
    examples = [
        ("How do I implement user authentication in Django?", "Authentication discussion"),
        ("Fix the login bug in @src/auth.py", "Bug fix session"),
        ("Explain async/await in Python", "Learning session"),
        ("Help me optimize database queries", "Performance optimization"),
        ("What's the best way to structure a React app?", "Architecture discussion"),
        ("Debug @server.js memory leak", "Debugging session"),
        ("Create a REST API with FastAPI", "API development"),
        ("Review @app/models.py and suggest improvements", "Code review"),
    ]
    
    for i, (user_input, context) in enumerate(examples, 1):
        title = _generate_session_title_from_prompt(user_input)
        
        print(f"Example {i}: {context}")
        print(f"  User's first message:")
        print(f"    '{user_input}'")
        print(f"  Generated session title:")
        print(f"    '{title}'")
        print()
    
    print("=" * 80)
    print("HOW IT WORKS")
    print("=" * 80)
    print()
    print("1. User starts a new session and sends their first message")
    print("2. After the AI responds, the system automatically:")
    print("   • Extracts the first 3-4 words from the user's message")
    print("   • Removes file attachments (@file syntax)")
    print("   • Cleans up extra whitespace")
    print("   • Saves the title to the session metadata")
    print()
    print("3. The title appears in /sessions list:")
    print()
    print("   BEFORE:")
    print("     6db601d1d4 (unnamed)")
    print("       codex • 3 turns • 2026-04-17 10:30:15")
    print()
    print("   AFTER:")
    print("     6db601d1d4 How do I implement")
    print("       codex • 3 turns • 2026-04-17 10:30:15")
    print()
    
    print("=" * 80)
    print("FEATURES")
    print("=" * 80)
    print()
    print("✓ Automatic - No user action required")
    print("✓ Smart - Removes file attachments and cleans text")
    print("✓ Preserves manual labels - /new [label] still works")
    print("✓ Handles edge cases - Short prompts get 'Quick chat' fallback")
    print("✓ Backward compatible - Existing sessions unchanged")
    print("✓ Zero performance impact - Simple string processing")
    print()
    
    print("=" * 80)
    print("USAGE")
    print("=" * 80)
    print()
    print("Just use the TUI normally:")
    print()
    print("  $ mtp tui")
    print("  > How do I implement JWT authentication?")
    print("  [AI responds...]")
    print()
    print("  $ mtp tui")
    print("  > /sessions")
    print()
    print("  Saved Sessions")
    print("  ──────────────────────────────────────────────────────────")
    print("  a4ec81e How do I implement")
    print("    openai • 1 turns • 2026-04-17 14:25:30")
    print()
    print("  Manual labels still work:")
    print()
    print("  $ mtp tui")
    print("  > /new My Important Project")
    print("  ✓ Started new chat chat-b2f3c8a9e1 (My Important Project).")
    print()
    
    print("=" * 80)
    print("TECHNICAL DETAILS")
    print("=" * 80)
    print()
    print("Implementation:")
    print("  • Function: _generate_session_title_from_prompt()")
    print("  • Location: src/mtp/cli/tui.py (after _new_session_id)")
    print("  • Trigger: After first turn is recorded")
    print("  • Storage: session.metadata['tui']['session_label']")
    print()
    print("Logic:")
    print("  if len(state.transcript) == 1 and not state.session_label:")
    print("      state.session_label = _generate_session_title_from_prompt(raw)")
    print("      _save_tui_session(state)")
    print()
    
    print("=" * 80)
    print("✓ FEATURE DEMONSTRATION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    demo()
