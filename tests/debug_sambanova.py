import httpx
import os
import pathlib
from dotenv import load_dotenv

# Load key
load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
api_key = os.getenv("SAMBANOVA_API_KEY")

print(f"--- Debugging SambaNova ---")
url = "https://api.sambanova.ai/v1/chat/completions"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}
data = {
    "model": "Meta-Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "hi"}]
}

try:
    response = httpx.post(url, headers=headers, json=data)
    print(f"Status Code: {response.status_code}")
    print(f"Response Headers: {response.headers}")
    print(f"Response Body: {response.text}")
except Exception as e:
    print(f"Error: {e}")
