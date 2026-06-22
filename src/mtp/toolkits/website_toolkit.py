from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from ..protocol import ToolRiskLevel, ToolSpec
from ..runtime import RegisteredTool, ToolkitLoader
from .common import allow_ref


class WebsiteToolkit(ToolkitLoader):
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "MTP-WebsiteToolkit/1.0",
        default_max_length: int = 5000,
        allowed_hosts: set[str] | None = None,
        allow_private_hosts: bool = False,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.default_max_length = default_max_length
        self.allowed_hosts = {host.lower() for host in allowed_hosts} if allowed_hosts else None
        self.allow_private_hosts = allow_private_hosts

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("URL must use http or https.")
        if not parsed.hostname:
            raise ValueError("URL must include a hostname.")
        host = parsed.hostname.lower()
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            raise ValueError(f"Host is not allowlisted: {host}")
        if self.allow_private_hosts:
            return url

        if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
            raise ValueError("Refusing to fetch localhost URLs.")

        addresses: set[str] = set()
        try:
            ip = ipaddress.ip_address(host)
            addresses.add(str(ip))
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            except socket.gaierror as exc:
                raise ValueError(f"Could not resolve host: {host}") from exc
            for family, _socktype, _proto, _canonname, sockaddr in infos:
                if family in {socket.AF_INET, socket.AF_INET6} and sockaddr:
                    addresses.add(str(sockaddr[0]))

        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError(f"Refusing to fetch private or non-public address: {address}")
        return url

    def _read_website(self, url: str, max_length: int) -> dict[str, Any]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ImportError(
                "WebsiteToolkit requires `requests` and `beautifulsoup4`. Install with: "
                "pip install requests beautifulsoup4"
            ) from exc

        safe_url = self._validate_url(url)
        response = requests.get(
            safe_url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else None
        text = " ".join(soup.stripped_strings)
        if max_length > 0:
            text = text[:max_length]
        return {"url": safe_url, "title": title, "text": text}

    def list_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="website.read_url",
                description="Read a URL and return extracted page text.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": allow_ref({"type": "string"}),
                        "max_length": allow_ref({"type": "integer"}),
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel.READ_ONLY,
            )
        ]

    def load_tools(self) -> list[RegisteredTool]:
        def read_url(url: str, max_length: int | None = None) -> dict[str, Any]:
            resolved_max_length = self.default_max_length if max_length is None else max_length
            return self._read_website(url=url, max_length=resolved_max_length)

        return [RegisteredTool(spec=self.list_tool_specs()[0], handler=read_url)]
