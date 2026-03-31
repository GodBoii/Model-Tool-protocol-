import os
import pathlib
from dotenv import load_dotenv

# Try to load from root, then current dir
load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
load_dotenv()

def test_p(name, test_fn):
    print(f"\n--- Testing {name} ---")
    try:
        test_fn()
    except Exception as e:
        print(f"❌ FAILED: {e}")

def check_openai():
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    res = client.chat.completions.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content": "hi"}], max_tokens=5)
    print(f"✅ SUCCESS: {res.choices[0].message.content}")

def check_gemini():
    from google import genai
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    res = client.models.generate_content(model="gemini-2.0-flash", contents="hi")
    print(f"✅ SUCCESS: {res.text}")

def check_anthropic():
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    res = client.messages.create(model="claude-3-haiku-20240307", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    print(f"✅ SUCCESS: {res.content[0].text}")

def check_openrouter():
    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
    res = client.chat.completions.create(model="qwen/qwen3.6-plus-preview:free", messages=[{"role": "user", "content": "hi"}], max_tokens=5)
    print(f"✅ SUCCESS: {res.choices[0].message.content}")

def check_groq():
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    res = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": "hi"}], max_tokens=5)
    print(f"✅ SUCCESS: {res.choices[0].message.content}")

def check_sambanova():
    from openai import OpenAI
    client = OpenAI(base_url="https://api.sambanova.ai/v1", api_key=os.getenv("SAMBANOVA_API_KEY"))
    try:
        res = client.chat.completions.create(model="Meta-Llama-3.1-8B-Instruct", messages=[{"role": "user", "content": "hi"}], max_tokens=10)
        print(f"✅ SUCCESS: {res.choices[0].message.content}")
    except Exception as e:
        print(f"❌ Detailed SambaNova Error: {e}")

if __name__ == "__main__":
    test_p("OpenAI", check_openai)
    test_p("Gemini", check_gemini)
    test_p("Anthropic", check_anthropic)
    test_p("OpenRouter (Free)", check_openrouter)
    test_p("Groq (Free/Fast)", check_groq)
    test_p("SambaNova", check_sambanova)
