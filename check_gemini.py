import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
key = os.getenv("GEMINI_API_KEY", "")
model = os.getenv("LLM_MODEL", "gemini-2.5-flash")
print("Key found:", bool(key), "| length:", len(key))
print("Model:", model)

for version in ("v1", "v1beta"):
    client = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(api_version=version),
    )
    try:
        r = client.models.generate_content(
            model=model,
            contents="Say hello in 3 words",
            config=types.GenerateContentConfig(
                system_instruction="You are a helpful assistant."
            ),
        )
        print(f"[{version}] SUCCESS:", r.text)
    except Exception as e:
        print(f"[{version}] FAILED:", e)