from dotenv import load_dotenv
load_dotenv()

import os
import google.generativeai as genai

API_KEY = os.getenv("GEMINI_API_KEY")
DEFAULT_MODEL_ENV = os.getenv("MODEL", "gemini-2.5-flash-lite").strip()

if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY is required")

genai.configure(api_key=API_KEY)

def _available_model_names() -> set[str]:
    names = set()
    for m in genai.list_models():
        methods = getattr(m, "supported_generation_methods", []) or []
        if "generateContent" in methods:
            full = m.name
            names.add(full)
            if full.startswith("models/"):
                names.add(full.replace("models/", ""))
    return names

CANDIDATES = [
    DEFAULT_MODEL_ENV or "",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-lite-latest",
    "gemini-2.5-flash",
    "gemini-2.5-flash-latest",
]

def resolve_model(request_model: str) -> str:
    avail = _available_model_names()
    wanted = (request_model or "").strip()
    if wanted and wanted in avail:
        return wanted
    for c in CANDIDATES:
        if c and c in avail:
            return c
    raise RuntimeError(f"No supported model found. Available: {sorted(avail)}")

class GeminiProvider:
    def generate(self, model: str, prompt: str) -> str:
        use_model = resolve_model(model)
        gm = genai.GenerativeModel(use_model)
        resp = gm.generate_content(prompt)
        return getattr(resp, "text", "")


