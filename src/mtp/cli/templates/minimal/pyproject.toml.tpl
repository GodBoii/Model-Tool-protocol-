[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{{PROJECT_NAME}}"
version = "0.1.0"
description = "MTP project scaffold: minimal agent"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
  "mtpx",
  "python-dotenv",
]

[project.optional-dependencies]
groq = ["groq"]
openai = ["openai"]
anthropic = ["anthropic"]
gemini = ["google-genai"]
cohere = ["cohere"]

[tool.setuptools]
py-modules = ["app"]

