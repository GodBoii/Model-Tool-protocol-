"""
Local Provider Management for MTP TUI

This module handles discovery, configuration, and management of local LLM providers
(Ollama, LMStudio) that can run on localhost or remote servers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
import json


# ── Provider Type Classification ─────────────────────────────────────────────

LOCAL_CAPABLE_PROVIDERS = {"ollama", "lmstudio"}
CLOUD_ONLY_PROVIDERS = {
    "openai", "groq", "claude", "gemini", "openrouter",
    "mistral", "cohere", "sambanova", "cerebras", "deepseek",
    "togetherai", "fireworksai",
}

DeploymentType = Literal["local", "cloud"]


def is_local_capable_provider(provider_name: str) -> bool:
    """Check if provider supports local deployment."""
    return provider_name.lower() in LOCAL_CAPABLE_PROVIDERS


def is_cloud_only_provider(provider_name: str) -> bool:
    """Check if provider only supports cloud deployment."""
    return provider_name.lower() in CLOUD_ONLY_PROVIDERS


# ── Default Endpoints ─────────────────────────────────────────────────────────

DEFAULT_LOCAL_ENDPOINTS: dict[str, str] = {
    "ollama": "http://localhost:11434",
    "lmstudio": "http://127.0.0.1:1234/v1",
}


def get_default_endpoint(provider_name: str, deployment_type: DeploymentType) -> str:
    """Get default endpoint for a provider based on deployment type."""
    if deployment_type == "local":
        return DEFAULT_LOCAL_ENDPOINTS.get(provider_name, "http://localhost:8000")
    # Cloud endpoints would be provider-specific
    return ""


# ── Model Discovery ───────────────────────────────────────────────────────────

@dataclass
class DiscoveredModel:
    """Represents a discovered model from a local provider."""
    name: str
    size: str | None = None
    modified: str | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None


@dataclass
class DiscoveryResult:
    """Result of model discovery operation."""
    success: bool
    models: list[DiscoveredModel]
    error_message: str | None = None
    endpoint: str | None = None


def discover_ollama_models(base_url: str, timeout: int = 5) -> DiscoveryResult:
    """
    Discover models from Ollama server.
    
    Args:
        base_url: Ollama server URL (e.g., "http://localhost:11434")
        timeout: Request timeout in seconds
    
    Returns:
        DiscoveryResult with discovered models or error
    """
    try:
        import requests
    except ImportError:
        return DiscoveryResult(
            success=False,
            models=[],
            error_message="requests library not installed. Install with: pip install requests",
        )
    
    # Normalize URL
    base_url = base_url.rstrip("/")
    tags_url = f"{base_url}/api/tags"
    
    try:
        response = requests.get(tags_url, timeout=timeout)
        response.raise_for_status()
        
        data = response.json()
        models_data = data.get("models", [])
        
        if not models_data:
            return DiscoveryResult(
                success=True,
                models=[],
                error_message="No models found. Pull a model with: ollama pull llama3.2:3b",
                endpoint=base_url,
            )
        
        discovered_models = []
        for model_info in models_data:
            name = model_info.get("name", "unknown")
            size_bytes = model_info.get("size", 0)
            
            # Format size
            size_str = _format_bytes(size_bytes) if size_bytes else None
            
            # Extract metadata
            details = model_info.get("details", {})
            family = details.get("family")
            parameter_size = details.get("parameter_size")
            quantization = details.get("quantization_level")
            
            # Modified time
            modified = model_info.get("modified_at", "")
            if modified:
                # Parse ISO timestamp to readable format
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(modified.replace("Z", "+00:00"))
                    modified = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass
            
            discovered_models.append(
                DiscoveredModel(
                    name=name,
                    size=size_str,
                    modified=modified,
                    family=family,
                    parameter_size=parameter_size,
                    quantization=quantization,
                )
            )
        
        return DiscoveryResult(
            success=True,
            models=discovered_models,
            endpoint=base_url,
        )
    
    except requests.exceptions.ConnectionError:
        return DiscoveryResult(
            success=False,
            models=[],
            error_message=f"Cannot connect to Ollama server at {base_url}. Is Ollama running?",
            endpoint=base_url,
        )
    except requests.exceptions.Timeout:
        return DiscoveryResult(
            success=False,
            models=[],
            error_message=f"Connection timeout to {base_url}. Server may be slow or unreachable.",
            endpoint=base_url,
        )
    except requests.exceptions.HTTPError as e:
        return DiscoveryResult(
            success=False,
            models=[],
            error_message=f"HTTP error from Ollama server: {e}",
            endpoint=base_url,
        )
    except Exception as e:
        return DiscoveryResult(
            success=False,
            models=[],
            error_message=f"Unexpected error: {e}",
            endpoint=base_url,
        )


def discover_lmstudio_models(base_url: str, timeout: int = 5) -> DiscoveryResult:
    """
    Discover models from LM Studio server.
    
    Args:
        base_url: LM Studio server URL (e.g., "http://127.0.0.1:1234/v1")
        timeout: Request timeout in seconds
    
    Returns:
        DiscoveryResult with discovered models or error
    """
    try:
        from openai import OpenAI
    except ImportError:
        return DiscoveryResult(
            success=False,
            models=[],
            error_message="openai library not installed. Install with: pip install openai",
        )
    
    # Normalize URL
    base_url = base_url.rstrip("/")
    
    try:
        client = OpenAI(base_url=base_url, api_key="lm-studio", timeout=timeout)
        models_response = client.models.list()
        
        models_data = models_response.data
        
        if not models_data:
            return DiscoveryResult(
                success=True,
                models=[],
                error_message="No models loaded. Load a model in LM Studio desktop app.",
                endpoint=base_url,
            )
        
        discovered_models = []
        for model_info in models_data:
            name = model_info.id
            
            # LM Studio doesn't provide size info via API
            # We can only get the model ID
            discovered_models.append(
                DiscoveredModel(
                    name=name,
                    size=None,
                    modified=None,
                )
            )
        
        return DiscoveryResult(
            success=True,
            models=discovered_models,
            endpoint=base_url,
        )
    
    except Exception as e:
        error_msg = str(e)
        if "Connection" in error_msg or "connect" in error_msg.lower():
            return DiscoveryResult(
                success=False,
                models=[],
                error_message=f"Cannot connect to LM Studio at {base_url}. Is the local server running?",
                endpoint=base_url,
            )
        return DiscoveryResult(
            success=False,
            models=[],
            error_message=f"Error discovering LM Studio models: {e}",
            endpoint=base_url,
        )


def discover_models(provider_name: str, base_url: str, timeout: int = 5) -> DiscoveryResult:
    """
    Discover models from a local provider.
    
    Args:
        provider_name: Provider name ("ollama" or "lmstudio")
        base_url: Server URL
        timeout: Request timeout in seconds
    
    Returns:
        DiscoveryResult with discovered models or error
    """
    provider_name = provider_name.lower()
    
    if provider_name == "ollama":
        return discover_ollama_models(base_url, timeout)
    elif provider_name == "lmstudio":
        return discover_lmstudio_models(base_url, timeout)
    else:
        return DiscoveryResult(
            success=False,
            models=[],
            error_message=f"Unknown local provider: {provider_name}",
        )


# ── Connection Health Checks ──────────────────────────────────────────────────

@dataclass
class HealthCheckResult:
    """Result of provider health check."""
    is_healthy: bool
    message: str
    endpoint: str
    model_count: int = 0


def check_ollama_health(base_url: str, timeout: int = 3) -> HealthCheckResult:
    """Check if Ollama server is healthy and reachable."""
    discovery = discover_ollama_models(base_url, timeout)
    
    if discovery.success:
        model_count = len(discovery.models)
        if model_count > 0:
            return HealthCheckResult(
                is_healthy=True,
                message=f"Connected ({model_count} models available)",
                endpoint=base_url,
                model_count=model_count,
            )
        else:
            return HealthCheckResult(
                is_healthy=False,
                message="Server running but no models pulled",
                endpoint=base_url,
                model_count=0,
            )
    else:
        return HealthCheckResult(
            is_healthy=False,
            message=discovery.error_message or "Connection failed",
            endpoint=base_url,
            model_count=0,
        )


def check_lmstudio_health(base_url: str, timeout: int = 3) -> HealthCheckResult:
    """Check if LM Studio server is healthy and reachable."""
    discovery = discover_lmstudio_models(base_url, timeout)
    
    if discovery.success:
        model_count = len(discovery.models)
        if model_count > 0:
            return HealthCheckResult(
                is_healthy=True,
                message=f"Connected ({model_count} models loaded)",
                endpoint=base_url,
                model_count=model_count,
            )
        else:
            return HealthCheckResult(
                is_healthy=False,
                message="Server running but no models loaded",
                endpoint=base_url,
                model_count=0,
            )
    else:
        return HealthCheckResult(
            is_healthy=False,
            message=discovery.error_message or "Connection failed",
            endpoint=base_url,
            model_count=0,
        )


def check_provider_health(provider_name: str, base_url: str, timeout: int = 3) -> HealthCheckResult:
    """Check health of a local provider."""
    provider_name = provider_name.lower()
    
    if provider_name == "ollama":
        return check_ollama_health(base_url, timeout)
    elif provider_name == "lmstudio":
        return check_lmstudio_health(base_url, timeout)
    else:
        return HealthCheckResult(
            is_healthy=False,
            message=f"Unknown provider: {provider_name}",
            endpoint=base_url,
            model_count=0,
        )


# ── Utility Functions ─────────────────────────────────────────────────────────

def _format_bytes(size_bytes: int) -> str:
    """Format bytes to human-readable size."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}PB"


# ── Setup Instructions ────────────────────────────────────────────────────────

SETUP_INSTRUCTIONS: dict[str, list[str]] = {
    "ollama": [
        "1. Install Ollama from: https://ollama.com",
        "2. Start Ollama service (usually starts automatically)",
        "3. Pull a model: ollama pull llama3.2:3b",
        "4. Verify: ollama list",
    ],
    "lmstudio": [
        "1. Download LM Studio from: https://lmstudio.ai",
        "2. Install and launch the desktop app",
        "3. Download a model from the model browser",
        "4. Load the model (click the model in 'My Models')",
        "5. Enable local server: Developer → Local Server → Start",
    ],
}


def get_setup_instructions(provider_name: str) -> list[str]:
    """Get setup instructions for a provider."""
    return SETUP_INSTRUCTIONS.get(provider_name.lower(), [])
