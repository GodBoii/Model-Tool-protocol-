import pathlib
import sys
import os

# Add src to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import Agent, Audio, File, Image, ToolRegistry, Video, load_dotenv_if_available
from mtp.providers import OpenRouter
from mtp.toolkits import CalculatorToolkit


def main() -> None:
    load_dotenv_if_available()

    tools = ToolRegistry()
    tools.register_toolkit_loader("calculator", CalculatorToolkit())

    # Pick a multimodal-capable OpenRouter model.
    # Override via OPENROUTER_MULTIMODAL_MODEL in .env.
    model = os.getenv("OPENROUTER_MULTIMODAL_MODEL", "google/gemini-2.5-flash")
    provider = OpenRouter(
        model=model,
        site_name="MTP Multimodal Demo",
    )
    agent = Agent(provider=provider, tools=tools, debug_mode=True)

    image = Image(url="https://upload.wikimedia.org/wikipedia/commons/0/0c/GoldenGateBridge-001.jpg")
    note = File(content="Quarterly growth: Q1 12%, Q2 15%, Q3 19%", filename="note.txt", mime_type="text/plain")
    audio = Audio(content=b"RIFF....WAVEfmt ", format="wav")
    video = Video(url="https://example.com/sample.mp4", format="mp4")

    print(f"Using OpenRouter model: {model}")
    try:
        response = agent.run(
            "Describe the image briefly, summarize the note, and compute (12 * 19).",
            images=[image],
            files=[note],
            audios=[audio],
            videos=[video],
        )
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)
        if "No endpoints found that support image input" in error_text:
            print("\nModel route does not support image/media input.")
            print("Set OPENROUTER_MULTIMODAL_MODEL in .env to a multimodal-capable model, for example:")
            print("  OPENROUTER_MULTIMODAL_MODEL=google/gemini-2.5-flash")
            print("  OPENROUTER_MULTIMODAL_MODEL=openai/gpt-4o-mini")
            raise SystemExit(2) from exc
        raise
    print(response)


if __name__ == "__main__":
    main()
