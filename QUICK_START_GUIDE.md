# 🚀 Multi-Provider TUI - Quick Start Guide

## ✅ System Status: WORKING!

The multi-provider TUI system is now fully functional and ready to use!

---

## 📋 Prerequisites

### Required
- Python 3.10+
- MTP SDK installed: `pip install -e .`

### Optional (Install as needed)
```bash
# Install provider SDKs you want to use:
pip install openai      # For OpenAI
pip install groq        # For Groq
pip install anthropic   # For Claude
pip install google-genai # For Gemini

# Or use the convenience extras:
pip install "mtpx[openai]"
pip install "mtpx[groq]"
pip install "mtpx[anthropic]"
pip install "mtpx[gemini]"
```

**Note**: You only need to install SDKs for providers you want to use. The system will gracefully handle missing SDKs.

---

## 🎯 Quick Start

### 1. Launch TUI
```bash
mtp tui
```

### 2. List Available Providers
```
/backend
```

You'll see all 13 providers (Codex + 12 MTP providers):
```
  Available Providers
  ──────────────────────────────────────────────────────────────────

  ● codex          gpt-5.3-codex                  ✓ Ready
  ○ openai         (not configured)               ⚠ Setup needed
  ○ groq           (not configured)               ⚠ Setup needed
  ○ claude         (not configured)               ⚠ Setup needed
  ...
```

### 3. Switch to a Provider
```
/backend groq
```

**First Time Setup**:
```
  Setup groq
  ────────────────────────────────────────────────────────

  Step 1: API Key
  Please provide your API key for groq.
  ▸ API Key: gsk_...

  Step 2: Model Selection
  Default: llama-3.3-70b-versatile
  ▸ Model (press Enter for default): 

  ✓ Provider groq configured successfully!

✓ Switched to groq with model llama-3.3-70b-versatile.
```

### 4. Start Chatting!
```
> Explain quantum computing in simple terms
```

---

## 📚 Command Reference

### Backend Management

| Command | Description | Example |
|---------|-------------|---------|
| `/backend` | List all providers | `/backend` |
| `/backend <provider>` | Switch to provider | `/backend groq` |

**Supported Providers**:
- `codex` - OpenAI Codex CLI (external binary)
- `openai` - OpenAI GPT models
- `groq` - Groq (fast inference)
- `claude` - Anthropic Claude
- `gemini` - Google Gemini
- `openrouter` - OpenRouter (multi-model)
- `mistral` - Mistral AI
- `cohere` - Cohere
- `sambanova` - SambaNova
- `cerebras` - Cerebras
- `deepseek` - DeepSeek
- `togetherai` - Together AI
- `fireworksai` - Fireworks AI

### Model Management

| Command | Description | Example |
|---------|-------------|---------|
| `/models` | Show available models | `/models` |
| `/model <name>` | Switch to model | `/model gpt-4o` |
| `/model add <name>` | Add custom model | `/model add gpt-4o-2024-08-06` |

### Other Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/status` | Show current session state |
| `/history` | Show conversation history |
| `/clear` | Clear screen |
| `/exit` | Exit TUI |

---

## 🔧 Configuration

### Settings Location
```
tmp/mtp_json_db/tui_provider_settings.json
```

### Settings Format
```json
{
  "providers": {
    "groq": {
      "api_key": "gsk_...",
      "model": "llama-3.3-70b-versatile",
      "models": [
        "llama-3.3-70b-versatile",
        "mixtral-8x7b-32768"
      ]
    },
    "openai": {
      "api_key": "sk-proj-...",
      "model": "gpt-4o",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  }
}
```

---

## 💡 Usage Examples

### Example 1: Use Groq for Fast Responses
```bash
# Switch to Groq
/backend groq

# Ask a question
> What are the key differences between Python and JavaScript?
```

### Example 2: Use Claude for Complex Reasoning
```bash
# Switch to Claude
/backend claude

# Ask a complex question
> Analyze the trade-offs between microservices and monolithic architecture
```

### Example 3: Use OpenAI with Custom Model
```bash
# Switch to OpenAI
/backend openai

# Add a custom model
/model add gpt-4o-2024-08-06

# Switch to it
/model gpt-4o-2024-08-06

# Use it
> Explain the latest features in this model
```

### Example 4: Compare Providers
```bash
# Ask the same question to different providers

/backend groq
> Explain quantum entanglement

/backend claude
> Explain quantum entanglement

/backend openai
> Explain quantum entanglement
```

---

## 🐛 Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'mistralai'"

**Cause**: Provider SDK not installed

**Solution**:
```bash
pip install "mtpx[mistral]"
# or
pip install mistralai
```

### Issue: "Provider 'xyz' requires the 'abc' package"

**Cause**: Missing provider SDK

**Solution**: Install the required package:
```bash
pip install "mtpx[xyz]"
```

### Issue: "Setup cancelled"

**Cause**: Pressed Ctrl+C during setup

**Solution**: Run `/backend <provider>` again to retry setup

### Issue: "Invalid API key"

**Cause**: Wrong API key provided

**Solution**: 
1. Get correct API key from provider's website
2. Run `/backend <provider>` again
3. Enter correct API key

### Issue: "Agent initialization failed"

**Cause**: Provider configuration issue

**Solution**:
1. Check API key is valid
2. Check model name is correct
3. Try switching to provider again: `/backend <provider>`

---

## 🎓 Tips & Tricks

### 1. Quick Provider Switching
You can switch providers mid-conversation:
```
> Explain machine learning
(response from Groq)

/backend claude
> Continue with more details
(response from Claude)
```

### 2. Custom Models
Add frequently-used models:
```
/model add gpt-4o-2024-08-06
/model add llama-3.1-405b-instruct
/model add claude-3-opus-20240229
```

### 3. Check Configuration
See what's configured:
```
/backend
```

### 4. Session Persistence
Your provider settings are saved automatically. When you restart TUI, your last provider and model are restored.

### 5. File Attachments
Attach files to your prompts:
```
> Explain the code in @src/main.py
```

---

## 📊 Provider Comparison

| Provider | Speed | Cost | Best For |
|----------|-------|------|----------|
| Groq | ⚡⚡⚡ | 💰 | Fast responses, simple tasks |
| OpenAI | ⚡⚡ | 💰💰💰 | General purpose, high quality |
| Claude | ⚡⚡ | 💰💰 | Complex reasoning, analysis |
| Gemini | ⚡⚡ | 💰 | Multimodal, free tier |
| OpenRouter | ⚡⚡ | 💰 | Access to many models |
| Mistral | ⚡⚡ | 💰💰 | European alternative |
| Cohere | ⚡⚡ | 💰💰 | Enterprise features |

---

## 🔐 Security Notes

### API Key Storage
- API keys are stored in `tui_provider_settings.json`
- File is in your local session database
- **Do not commit this file to git**
- Consider using environment variables for production

### Best Practices
1. Use separate API keys for development and production
2. Rotate API keys regularly
3. Set spending limits on provider dashboards
4. Don't share your settings file

---

## 🚀 Next Steps

### Install More Providers
```bash
pip install "mtpx[anthropic]"  # Claude
pip install "mtpx[gemini]"     # Gemini
pip install "mtpx[mistral]"    # Mistral
pip install "mtpx[cohere]"     # Cohere
```

### Explore Advanced Features
- Try autoresearch mode: `/autoresearch on`
- Adjust max rounds: `/rounds 10`
- Use file attachments: `@path/to/file`
- Check tool calls: `/tools`

### Read Documentation
- `MULTI_PROVIDER_TUI_ANALYSIS.md` - Complete architecture
- `PROVIDER_ARCHITECTURE_DIAGRAM.md` - Visual diagrams
- `IMPLEMENTATION_COMPLETE.md` - Implementation details

---

## 🎉 Success!

You're now ready to use the multi-provider TUI system!

**Quick Recap**:
1. ✅ Launch: `mtp tui`
2. ✅ List providers: `/backend`
3. ✅ Switch provider: `/backend groq`
4. ✅ Start chatting!

Enjoy exploring different AI providers! 🚀
