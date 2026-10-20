"""Configuration loader and strict validator with OWASP SSRF defense (CWE-918)."""

from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

FORBIDDEN_HOSTNAMES = {"localhost", "metadata.google.internal"}
METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}
NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check whether an IP address is private, loopback, link-local, or cloud metadata.

    Supports RFC 6052 NAT64 translation prefixes by unmapping the embedded IPv4 address.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip in NAT64_PREFIX:
        embedded_v4 = ipaddress.IPv4Address(ip.packed[-4:])
        return is_forbidden_ip(embedded_v4)

    if ip.is_loopback or ip.is_link_local or ip.is_private:
        return True

    ip_str = str(ip)
    if ip_str in METADATA_IPS:
        return True

    if isinstance(ip, ipaddress.IPv4Address):
        return bool(ip.is_unspecified or ip.is_multicast or ip.is_reserved)

    return False


def validate_target_domain(domain_or_url: str) -> str:
    """Validate target domain to prevent Server-Side Request Forgery (SSRF - CWE-918).

    Rejects non-HTTPS schemes, loopbacks, local hostnames, private IP ranges (RFC 1918),
    and cloud metadata IP addresses.
    """
    raw = domain_or_url.strip()
    if not raw:
        raise ValueError("Store domain cannot be empty")

    # If user provided a full URL, parse it
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme.lower() != "https":
            raise ValueError(f"SSRF Violation: Target protocol must be 'https', got '{parsed.scheme}'")
        hostname = parsed.hostname or ""
    else:
        # User provided just domain like "kith.com"
        hostname = raw.split("/")[0].split(":")[0]

    hostname = hostname.lower()

    if hostname in FORBIDDEN_HOSTNAMES or hostname.endswith((".local", ".internal")):
        raise ValueError(f"SSRF Violation: Forbidden private/internal hostname '{hostname}'")

    # Check if hostname is an IP address
    try:
        ip = ipaddress.ip_address(hostname)
        if is_forbidden_ip(ip):
            raise ValueError(f"SSRF Violation: Target IP '{ip}' is private, loopback, or metadata address")
    except ValueError as e:
        if "SSRF Violation" in str(e):
            raise

    # Resolve domain if DNS is available to catch DNS rebinding to private IPs
    try:
        addr_infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        for _, _, _, _, sockaddr in addr_infos:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if is_forbidden_ip(ip):
                raise ValueError(f"SSRF Violation: Resolved IP '{ip_str}' for domain '{hostname}' is non-routable")
    except (socket.gaierror, socket.herror, TimeoutError):
        # Allow unresolved domain names in offline/testing mode as long as domain syntax is valid
        pass

    # Ensure hostname conforms to standard domain name characters
    domain_pattern = re.compile(r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")
    if not domain_pattern.match(hostname):
        raise ValueError(f"Invalid store domain format: '{hostname}'")

    return hostname


class MonitorConfig(BaseModel):
    """Configuration for the Shopify poller."""

    model_config = ConfigDict(strict=True)

    store_domain: str = Field(..., description="Target Shopify store domain (e.g. kith.com)")
    collections: list[str] = Field(default_factory=list, description="List of collection handles to monitor")
    poll_interval_seconds: int = Field(default=15, ge=1, description="Interval between scrape passes in seconds")
    request_timeout_seconds: float = Field(default=8.0, ge=1.0, description="Network timeout per request in seconds")
    max_retries: int = Field(default=5, ge=1, description="Maximum retry count on 429 or transient errors")
    user_agents: list[str] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        ],
        description="Pool of rotating User-Agents",
    )

    @field_validator("store_domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        return validate_target_domain(v)


class DiscordConfig(BaseModel):
    """Discord visual presentation settings."""

    model_config = ConfigDict(strict=True)

    bot_username: str = Field(default="Shopify Sentinel", min_length=1)
    avatar_url: str = Field(
        default="https://cdn-icons-png.flaticon.com/512/3081/3081559.png",
        description="Avatar URL displayed in webhook messages",
    )
    footer_text: str = Field(
        default="Shopify Inventory Sentinel • Real-Time Daemon",
        description="Footer text shown on Discord embeds",
    )


class AppSettings(BaseSettings):
    """Primary application settings combining .env secrets and YAML configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    discord_webhook_url: str = Field(
        ...,
        alias="DISCORD_WEBHOOK_URL",
        description="Discord channel webhook endpoint",
    )
    proxy_url: str | None = Field(
        default=None,
        alias="PROXY_URL",
        description="Optional proxy server URL",
    )
    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="Logging verbosity level",
    )

    monitor: MonitorConfig
    discord: DiscordConfig

    @field_validator("discord_webhook_url")
    @classmethod
    def validate_webhook(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme != "https" or parsed.hostname not in {"discord.com", "discordapp.com"}:
            raise ValueError("DISCORD_WEBHOOK_URL must be a valid HTTPS Discord webhook URL")
        return v

    @classmethod
    def load(
        cls,
        config_path: Path | str = "config.yaml",
        env_file: Path | str = ".env",
    ) -> AppSettings:
        """Load settings by merging config.yaml and environment variables."""
        cfg_file = Path(config_path)
        if not cfg_file.is_file():
            raise FileNotFoundError(f"Configuration file not found: {cfg_file}")

        with open(cfg_file, "r", encoding="utf-8") as f:
            yaml_data: dict[str, Any] = yaml.safe_load(f) or {}

        monitor_data = yaml_data.get("monitor", {})
        discord_data = yaml_data.get("discord", {})

        # Load environment values explicitly from file if provided
        env_vals: dict[str, Any] = {}
        env_path = Path(env_file)
        if env_path.is_file():
            env_vals = {k: v for k, v in dotenv_values(env_path).items() if v is not None}

        merged_kwargs: dict[str, Any] = {
            **env_vals,
            "monitor": MonitorConfig(**monitor_data),
            "discord": DiscordConfig(**discord_data),
        }

        return cls(**merged_kwargs)
