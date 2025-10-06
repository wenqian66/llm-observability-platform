import os
from typing import Protocol

class Provider(Protocol):
    def generate(self, model: str, prompt: str) -> str: ...

REGISTRY: dict[str, Provider] = {}

def register(name: str, provider: Provider):
    REGISTRY[name] = provider

def get_provider():
    name = os.getenv("PROVIDER", "gemini")
    if name not in REGISTRY:
        raise RuntimeError(f"Provider '{name}' not found. Registered: {list(REGISTRY.keys())}")
    return REGISTRY[name]
