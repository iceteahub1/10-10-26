"""Local-only, validated application settings."""
from dataclasses import dataclass
from decimal import Decimal
import os
from urllib.parse import urlsplit
from dotenv import load_dotenv
from bistro.paths import ROOT
from bistro.ai_preferences import read_preferences, valid_model

@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    ollama_url: str
    model: str
    currency: str
    tax_rate: Decimal
    service_rate: Decimal
    ai_enabled: bool = True
    ai_transport: str = 'native'

def load_settings():
    load_dotenv(ROOT / ".env", override=False)
    host = os.getenv("BISTRO_HOST", "127.0.0.1")
    port = int(os.getenv("BISTRO_PORT", "8000"))
    url = os.getenv("BISTRO_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urlsplit(url)
    if host not in ("127.0.0.1", "localhost") or not 1 <= port <= 65535:
        raise ValueError("Dashboard must use a loopback host and port 1–65535.")
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("BISTRO_OLLAMA_URL must be a local HTTP origin.")
    preference = read_preferences()
    model = preference['model'] if preference else os.getenv("BISTRO_MODEL", "qwen2.5:7b")
    if not valid_model(model):
        raise ValueError("BISTRO_MODEL must specify an exact qwen2.5 tag.")
    tax = Decimal(os.getenv("BISTRO_TAX_RATE", "0"))
    service = Decimal(os.getenv("BISTRO_SERVICE_RATE", "0"))
    if not all(x.is_finite() and 0 <= x <= 1 for x in (tax, service)):
        raise ValueError("Tax/service rates must be fractions between 0 and 1.")
    return Settings(host, port, url, model, os.getenv("BISTRO_CURRENCY", "PHP"), tax, service,
                    preference['enabled'] if preference else True,
                    preference['transport'] if preference else 'native')
