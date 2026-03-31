import os
import pathlib
from openai import OpenAI
from dotenv import load_dotenv

# 1. Try to load from root, then from current directory
load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
load_dotenv() 

api_key = os.getenv("OPENROUTER_API_KEY")

if not api_key:
    print("❌ ERROR: OPENROUTER_API_KEY not found in .env file!")
    exit(1)

# 2. Setup the client
client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key=api_key,
)

print(f"--- Testing OpenRouter Connection ---")
try:
    # 3. Try a simple chat request
    completion = client.chat.completions.create(
      model="qwen/qwen3.6-plus-preview:free",
      messages=[
        {"role": "user", "content": "Say 'hello if you are working'"}
      ]
    )
    print(f"✅ SUCCESS! Response: {completion.choices[0].message.content}")
except Exception as e:
    print(f"❌ FAILED! Error type: {type(e).__name__}")
    print(f"Error Message: {e}")
