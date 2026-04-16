"""
Interactive Setup for Local Providers

This module handles the interactive setup flow for local LLM providers (Ollama, LMStudio).
It guides users through deployment type selection, model discovery, and configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tui import TUIState

from .tui_local_providers import (
    discover_models,
    get_default_endpoint,
    get_setup_instructions,
    is_local_capable_provider,
    DiscoveredModel,
    DiscoveryResult,
)
from .tui_settings import (
    ensure_provider_entry,
    load_provider_settings,
    provider_settings_path,
    save_provider_settings,
    set_base_url,
    set_deployment_type,
    set_discovered_models,
    set_preferred_model,
)

# Import theme/colors from TUI
try:
    from .tui_theme import (
        C_ACCENT, C_BORDER, C_BRAND_BOLD, C_CMD, C_DIM, C_ERROR,
        C_HIGHLIGHT, C_KEY, C_LABEL, C_MODEL, C_SUCCESS, C_TEXT,
        C_VALUE, C_WARNING, RESET,
        SYM_BULLET, SYM_DOT, SYM_ERR, SYM_INFO, SYM_OK, SYM_RULE, SYM_WARN,
        get_term_width,
    )
except ImportError:
    # Fallback if theme module not available
    C_ACCENT = C_BORDER = C_BRAND_BOLD = C_CMD = C_DIM = C_ERROR = ""
    C_HIGHLIGHT = C_KEY = C_LABEL = C_MODEL = C_SUCCESS = C_TEXT = ""
    C_VALUE = C_WARNING = RESET = ""
    SYM_BULLET = "•"
    SYM_DOT = "·"
    SYM_ERR = "✗"
    SYM_INFO = "ℹ"
    SYM_OK = "✓"
    SYM_RULE = "─"
    SYM_WARN = "⚠"
    
    def get_term_width() -> int:
        return 80


def setup_local_provider_interactive(state: TUIState, provider_name: str) -> tuple[bool, str]:
    """
    Interactive setup flow for local providers.
    
    Flow:
    1. Ask: Local or Cloud deployment?
    2. If Local:
       a. Use default endpoint or custom?
       b. Discover models from endpoint
       c. Let user select model
       d. Save configuration
    3. If Cloud:
       a. Ask for API key
       b. Ask for endpoint (optional)
       c. Ask for model name
       d. Save configuration
    
    Args:
        state: TUI state
        provider_name: Provider name ("ollama" or "lmstudio")
    
    Returns:
        (success, message) tuple
    """
    provider_name = provider_name.lower()
    
    if not is_local_capable_provider(provider_name):
        return False, f"{C_ERROR}Provider {provider_name} does not support local deployment.{RESET}"
    
    # Step 1: Ask deployment type
    print()
    print(f"  {C_BRAND_BOLD}{provider_name.title()} Setup{RESET}")
    print(f"  {C_BORDER}{SYM_RULE * 60}{RESET}")
    print()
    print(f"  {C_LABEL}Deployment Type:{RESET}")
    print(f"    {C_KEY}[1]{RESET} {C_HIGHLIGHT}Local{RESET} {C_DIM}(recommended) - Run on your machine{RESET}")
    print(f"    {C_KEY}[2]{RESET} {C_TEXT}Cloud/Remote{RESET} {C_DIM}- Connect to remote server{RESET}")
    print()
    
    try:
        choice = input(f"  {C_ACCENT}Select deployment type (1-2):{RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        return False, f"{C_WARNING}Setup cancelled.{RESET}"
    
    if choice == "1" or choice.lower() == "local":
        return _setup_local_deployment(state, provider_name)
    elif choice == "2" or choice.lower() in {"cloud", "remote"}:
        return _setup_cloud_deployment(state, provider_name)
    else:
        return False, f"{C_ERROR}Invalid choice. Setup cancelled.{RESET}"


def _setup_local_deployment(state: TUIState, provider_name: str) -> tuple[bool, str]:
    """Setup local deployment for a provider."""
    print()
    print(f"  {C_SUCCESS}{SYM_OK}{RESET} Local deployment selected")
    print()
    
    # Get default endpoint
    default_endpoint = get_default_endpoint(provider_name, "local")
    
    # Ask if user wants to use default or custom endpoint
    print(f"  {C_LABEL}Endpoint Configuration:{RESET}")
    print(f"    {C_DIM}Default: {C_VALUE}{default_endpoint}{RESET}")
    print()
    
    try:
        use_default = input(f"  {C_ACCENT}Use default endpoint? (Y/n):{RESET} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False, f"{C_WARNING}Setup cancelled.{RESET}"
    
    if use_default in {"n", "no"}:
        try:
            custom_endpoint = input(f"  {C_ACCENT}Enter custom endpoint URL:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            return False, f"{C_WARNING}Setup cancelled.{RESET}"
        
        if not custom_endpoint:
            return False, f"{C_ERROR}No endpoint provided. Setup cancelled.{RESET}"
        
        endpoint = custom_endpoint
    else:
        endpoint = default_endpoint
    
    print()
    print(f"  {C_DIM}Using endpoint: {C_VALUE}{endpoint}{RESET}")
    print()
    
    # Discover models
    print(f"  {C_ACCENT}{SYM_DOT}{RESET} Discovering models from {provider_name}...")
    
    discovery = discover_models(provider_name, endpoint, timeout=5)
    
    if not discovery.success:
        # Show error and setup instructions
        print()
        print(f"  {C_ERROR}{SYM_ERR} {discovery.error_message}{RESET}")
        print()
        print(f"  {C_LABEL}Setup Instructions:{RESET}")
        
        instructions = get_setup_instructions(provider_name)
        for instruction in instructions:
            print(f"    {C_DIM}{instruction}{RESET}")
        
        print()
        return False, f"{C_ERROR}Cannot connect to {provider_name}. Please follow setup instructions above.{RESET}"
    
    if not discovery.models:
        # No models found
        print()
        print(f"  {C_WARNING}{SYM_WARN} {discovery.error_message or 'No models found'}{RESET}")
        print()
        return False, f"{C_WARNING}No models available. Please load/pull a model first.{RESET}"
    
    # Display discovered models
    print()
    print(f"  {C_SUCCESS}{SYM_OK} Found {len(discovery.models)} model(s){RESET}")
    print()
    
    # Let user select model
    selected_model = _select_model_interactive(discovery.models, provider_name)
    
    if not selected_model:
        return False, f"{C_WARNING}No model selected. Setup cancelled.{RESET}"
    
    # Save configuration
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    
    set_deployment_type(settings, provider_name, "local")
    set_base_url(settings, provider_name, endpoint)
    set_preferred_model(settings, provider_name, selected_model)
    set_discovered_models(settings, provider_name, [m.name for m in discovery.models])
    
    save_provider_settings(settings_path, settings)
    
    print()
    print(f"  {C_SUCCESS}{SYM_OK} Configuration saved{RESET}")
    print()
    
    return True, (
        f"{C_SUCCESS}✓{RESET} {provider_name.title()} configured successfully!\n"
        f"  {C_LABEL}Deployment:{RESET} {C_VALUE}local{RESET}\n"
        f"  {C_LABEL}Endpoint:{RESET} {C_VALUE}{endpoint}{RESET}\n"
        f"  {C_LABEL}Model:{RESET} {C_MODEL}{selected_model}{RESET}"
    )


def _setup_cloud_deployment(state: TUIState, provider_name: str) -> tuple[bool, str]:
    """Setup cloud/remote deployment for a provider."""
    print()
    print(f"  {C_SUCCESS}{SYM_OK}{RESET} Cloud/Remote deployment selected")
    print()
    
    # Ask for endpoint
    print(f"  {C_LABEL}Remote Endpoint:{RESET}")
    try:
        endpoint = input(f"  {C_ACCENT}Enter server URL (e.g., https://my-server.com:11434):{RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        return False, f"{C_WARNING}Setup cancelled.{RESET}"
    
    if not endpoint:
        return False, f"{C_ERROR}No endpoint provided. Setup cancelled.{RESET}"
    
    # Ask for API key (optional)
    print()
    print(f"  {C_LABEL}API Key (optional):{RESET}")
    try:
        api_key = input(f"  {C_ACCENT}Enter API key (press Enter to skip):{RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        return False, f"{C_WARNING}Setup cancelled.{RESET}"
    
    # Try to discover models
    print()
    print(f"  {C_ACCENT}{SYM_DOT}{RESET} Discovering models from remote server...")
    
    discovery = discover_models(provider_name, endpoint, timeout=10)
    
    if discovery.success and discovery.models:
        # Models discovered - let user select
        print()
        print(f"  {C_SUCCESS}{SYM_OK} Found {len(discovery.models)} model(s){RESET}")
        print()
        
        selected_model = _select_model_interactive(discovery.models, provider_name)
        
        if not selected_model:
            return False, f"{C_WARNING}No model selected. Setup cancelled.{RESET}"
    else:
        # Cannot discover - ask user to enter model name manually
        print()
        print(f"  {C_WARNING}{SYM_WARN} Cannot discover models from server{RESET}")
        print(f"  {C_DIM}You'll need to enter the model name manually{RESET}")
        print()
        
        try:
            selected_model = input(f"  {C_ACCENT}Enter model name:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            return False, f"{C_WARNING}Setup cancelled.{RESET}"
        
        if not selected_model:
            return False, f"{C_ERROR}No model provided. Setup cancelled.{RESET}"
    
    # Save configuration
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    
    set_deployment_type(settings, provider_name, "cloud")
    set_base_url(settings, provider_name, endpoint)
    set_preferred_model(settings, provider_name, selected_model)
    
    if api_key:
        entry = ensure_provider_entry(settings, provider_name)
        entry["api_key"] = api_key
    
    if discovery.success and discovery.models:
        set_discovered_models(settings, provider_name, [m.name for m in discovery.models])
    
    save_provider_settings(settings_path, settings)
    
    print()
    print(f"  {C_SUCCESS}{SYM_OK} Configuration saved{RESET}")
    print()
    
    return True, (
        f"{C_SUCCESS}✓{RESET} {provider_name.title()} configured successfully!\n"
        f"  {C_LABEL}Deployment:{RESET} {C_VALUE}cloud{RESET}\n"
        f"  {C_LABEL}Endpoint:{RESET} {C_VALUE}{endpoint}{RESET}\n"
        f"  {C_LABEL}Model:{RESET} {C_MODEL}{selected_model}{RESET}"
    )


def _select_model_interactive(models: list[DiscoveredModel], provider_name: str) -> str | None:
    """
    Interactive model selection from discovered models.
    
    Args:
        models: List of discovered models
        provider_name: Provider name for context
    
    Returns:
        Selected model name or None if cancelled
    """
    w = get_term_width()
    rule_w = min(70, w - 4)
    
    print(f"  {C_LABEL}Available Models:{RESET}")
    print(f"  {C_BORDER}{SYM_RULE * rule_w}{RESET}")
    print()
    
    # Display models with numbers
    for idx, model in enumerate(models, start=1):
        # Format model info
        name_display = f"{C_MODEL}{model.name}{RESET}"
        
        # Add size if available
        info_parts = []
        if model.size:
            info_parts.append(f"{C_DIM}{model.size}{RESET}")
        if model.parameter_size:
            info_parts.append(f"{C_DIM}{model.parameter_size}{RESET}")
        if model.modified:
            info_parts.append(f"{C_DIM}modified: {model.modified}{RESET}")
        
        info_display = "  ".join(info_parts) if info_parts else ""
        
        # Recommend first model
        recommend = f"  {C_SUCCESS}← Recommended{RESET}" if idx == 1 else ""
        
        print(f"    {C_KEY}[{idx}]{RESET} {name_display}  {info_display}{recommend}")
    
    print()
    print(f"  {C_BORDER}{SYM_RULE * rule_w}{RESET}")
    print()
    
    # Get user selection
    try:
        choice = input(f"  {C_ACCENT}Select model (1-{len(models)} or model name):{RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    
    # Try to parse as number
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(models):
            selected = models[idx - 1].name
            print()
            print(f"  {C_SUCCESS}{SYM_OK} Selected: {C_MODEL}{selected}{RESET}")
            return selected
        else:
            print()
            print(f"  {C_ERROR}{SYM_ERR} Invalid selection{RESET}")
            return None
    
    # Try to match model name
    choice_lower = choice.lower()
    for model in models:
        if model.name.lower() == choice_lower or choice_lower in model.name.lower():
            print()
            print(f"  {C_SUCCESS}{SYM_OK} Selected: {C_MODEL}{model.name}{RESET}")
            return model.name
    
    # No match
    print()
    print(f"  {C_ERROR}{SYM_ERR} Model not found: {choice}{RESET}")
    return None


def refresh_local_models(state: TUIState, provider_name: str) -> tuple[bool, str]:
    """
    Refresh the list of available models for a local provider.
    
    Args:
        state: TUI state
        provider_name: Provider name
    
    Returns:
        (success, message) tuple
    """
    provider_name = provider_name.lower()
    
    if not is_local_capable_provider(provider_name):
        return False, f"{C_ERROR}Provider {provider_name} does not support local deployment.{RESET}"
    
    # Load settings
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    entry = ensure_provider_entry(settings, provider_name)
    
    deployment_type = entry.get("deployment_type")
    if deployment_type != "local":
        return False, f"{C_WARNING}Provider is not configured for local deployment.{RESET}"
    
    base_url = entry.get("base_url")
    if not base_url:
        return False, f"{C_ERROR}No endpoint configured for {provider_name}.{RESET}"
    
    # Discover models
    print(f"  {C_ACCENT}{SYM_DOT}{RESET} Refreshing models from {provider_name}...")
    
    discovery = discover_models(provider_name, base_url, timeout=5)
    
    if not discovery.success:
        return False, f"{C_ERROR}Failed to discover models: {discovery.error_message}{RESET}"
    
    if not discovery.models:
        return False, f"{C_WARNING}No models found.{RESET}"
    
    # Update discovered models list
    set_discovered_models(settings, provider_name, [m.name for m in discovery.models])
    save_provider_settings(settings_path, settings)
    
    # Display discovered models
    model_names = [m.name for m in discovery.models]
    models_display = ", ".join(f"{C_MODEL}{name}{RESET}" for name in model_names[:5])
    if len(model_names) > 5:
        models_display += f" {C_DIM}(+{len(model_names) - 5} more){RESET}"
    
    return True, (
        f"{C_SUCCESS}{SYM_OK} Refreshed {len(discovery.models)} model(s){RESET}\n"
        f"  {models_display}"
    )
