"""Typed errors for the discovery and condensation pipeline."""
from __future__ import annotations


class DiscoveryError(Exception):
    """Base class for all pipeline errors."""


class SpecNetworkError(DiscoveryError):
    """Target host is unreachable."""


class SpecParseError(DiscoveryError):
    """Found a spec-like file but could not parse it."""
    def __init__(self, path: str):
        self.spec_path = path
        super().__init__(f"Could not parse spec at {path}")


class SpecEmptyError(DiscoveryError):
    """Spec parsed successfully but contained no endpoints."""
    def __init__(self, path: str = ""):
        self.spec_path = path
        super().__init__(f"Spec at {path} has no parseable endpoints")


class PlaywrightNotInstalledError(DiscoveryError):
    """Playwright is not installed."""


class TrafficNetworkError(DiscoveryError):
    """Could not load the target page."""


class NoXHRCapturedError(DiscoveryError):
    """Page loaded but made no XHR/fetch calls (server-rendered)."""


class AllTrackersFilteredError(DiscoveryError):
    """XHR calls seen but all matched the tracker blocklist."""


class BrandFilteredError(DiscoveryError):
    """XHR calls seen but none matched the service brand."""
    def __init__(self, base_brand: str, seen_brands: list[str]):
        self.base_brand = base_brand
        self.seen_brands = seen_brands
        super().__init__(f"No XHR calls matched brand '{base_brand}'")


class LLMQuotaError(DiscoveryError):
    """All AI providers exhausted their quota."""


class LLMInvalidResponseError(DiscoveryError):
    """AI returned a response that could not be parsed as valid JSON."""


class LLMEmptySchemaError(DiscoveryError):
    """AI returned valid JSON but with no tools."""
