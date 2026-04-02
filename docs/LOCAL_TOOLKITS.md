# Local Toolkits

MTP includes local/no-key toolkits inspired by common agent toolkit patterns:
- calculator
- file
- python
- shell

MTP also includes optional search/web-scrape toolkits inspired by Agno:
- wikipedia
- website
- newspaper
- newspaper4k
- crawl4ai

Use them with:

```python
from mtp import ToolRegistry, register_local_toolkits

registry = ToolRegistry()
register_local_toolkits(registry, base_dir=".")
```

## Discovery + lazy loading

- Tool names are discoverable through loader spec preview.
- Handlers load only when a matching tool is called.

## Toolkit summary

## `calculator.*`
- `calculator.add`
- `calculator.subtract`
- `calculator.multiply`
- `calculator.divide`
- `calculator.sqrt`

## `file.*`
- `file.list_files`
- `file.read_file`
- `file.write_file`
- `file.search_in_files`

Paths are constrained to the configured `base_dir`.

## `python.*`
- `python.run_code`
- `python.run_file`

Execution defaults to isolated subprocess mode (`python -I`) with timeout.
Unsafe in-process `exec` mode is opt-in via `allow_unsafe_exec=True`.

## `shell.*`
- `shell.run_command`

Runs commands in `base_dir` with timeout and an allowlist (`echo`, `pwd`, `ls`, `dir` by default).
Use `allowed_commands=` to customize.

## Search + web-scrape toolkits

These are dependency-optional and lazily loaded. You can register them without installing packages, but the first call will fail with an install hint if dependencies are missing.

## `wikipedia.*`
- `wikipedia.search_wikipedia`

Dependency:
- `pip install wikipedia`

## `website.*`
- `website.read_url`

Dependencies:
- `pip install requests beautifulsoup4`

## `newspaper.*`
- `newspaper.get_article_text`

Dependencies:
- `pip install newspaper3k lxml_html_clean`

## `newspaper4k.*`
- `newspaper4k.read_article`

Dependencies:
- `pip install newspaper4k lxml_html_clean`

## `crawl4ai.*`
- `crawl4ai.web_crawler`

Dependency:
- `pip install crawl4ai`

## Risk and policy

Default policy:
- read-only tools: allow
- write tools: allow
- destructive tools: ask

Customize with `RiskPolicy` when creating `ToolRegistry`.
