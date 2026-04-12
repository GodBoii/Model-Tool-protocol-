"use client";

import { cn } from "@/lib/utils";
import { Check, Copy, ChevronDown, ChevronUp, Terminal } from "lucide-react";
import { useState, useMemo } from "react";

interface CodeBlockProps {
  code: string;
  language?: string;
  className?: string;
  label?: string;
  output?: string;
  outputLabel?: string;
}

// ── UNIFIED VIBRANT COLORS ──
const COLORS = {
  prompt: "text-white/50 font-bold",
  keyword: "text-[#c678dd]",      // Purple
  builtin: "text-[#d19a66]",      // Peach/Builtin
  string: "text-[#98c379]",       // Green
  number: "text-[#d19a66]",       // Orange
  comment: "text-white/25 italic font-light",
  decorator: "text-[#61afef]",    // Blue
  function: "text-[#61afef]",     // Blue
  operator: "text-[#56b6c2]",     // Cyan
  property: "text-[#e06c75]",     // Reddish
  type: "text-[#e5c07b]",         // Yellowish
  variable: "text-[#e06c75]",     // Reddish
  punctuation: "text-white/30",
  key: "text-[#61afef]",          // Blue
  boolean: "text-[#d19a66]",      // Orange
  default: "text-white/80",
};

// ── SYNTAX HIGHLIGHTER ──
function highlightCode(code: string, language: string): React.ReactNode[] {
  const lines = code.split("\n");

  return lines.map((line, lineIdx) => {
    const tokens = tokenizeLine(line, language);
    return (
      <span key={lineIdx}>
        {tokens}
        {lineIdx < lines.length - 1 ? "\n" : ""}
      </span>
    );
  });
}

function tokenizeLine(line: string, language: string): React.ReactNode[] {
  const tokens: React.ReactNode[] = [];
  let remaining = line;
  let keyIdx = 0;

  if (language === "bash") {
    return tokenizeBash(line);
  }
  if (language === "json") {
    return tokenizeJson(line);
  }
  if (language === "env") {
    return tokenizeEnv(line);
  }

  // Python / generic
  while (remaining.length > 0) {
    let matched = false;

    // Prompt detector (for REPL style)
    if (remaining.match(/^>>> /)) {
        tokens.push(<span key={keyIdx++} className={COLORS.prompt}>{">>> "}</span>);
        remaining = remaining.slice(4);
        matched = true;
    } else if (remaining.match(/^\.\.\. /)) {
        tokens.push(<span key={keyIdx++} className={COLORS.prompt}>{"... "}</span>);
        remaining = remaining.slice(4);
        matched = true;
    }

    // Comments
    if (!matched) {
      const commentMatch = remaining.match(/^(#.*)$/);
      if (commentMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.comment}>{commentMatch[1]}</span>);
        remaining = "";
        matched = true;
      }
    }

    // Decorators
    if (!matched) {
      const decoMatch = remaining.match(/^(@[\w.]+)/);
      if (decoMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.decorator}>{decoMatch[1]}</span>);
        remaining = remaining.slice(decoMatch[1].length);
        matched = true;
      }
    }

    // Triple-quoted strings
    if (!matched) {
      const tripleMatch = remaining.match(/^("""[\s\S]*?"""|'''[\s\S]*?''')/);
      if (tripleMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.string}>{tripleMatch[1]}</span>);
        remaining = remaining.slice(tripleMatch[1].length);
        matched = true;
      }
    }

    // F-strings and strings
    if (!matched) {
      const strMatch = remaining.match(/^(f?"[^"]*"|f?'[^']*'|"[^"]*"|'[^']*')/);
      if (strMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.string}>{strMatch[1]}</span>);
        remaining = remaining.slice(strMatch[1].length);
        matched = true;
      }
    }

    // Numbers
    if (!matched) {
      const numMatch = remaining.match(/^(\b\d+\.?\d*\b)/);
      if (numMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.number}>{numMatch[1]}</span>);
        remaining = remaining.slice(numMatch[1].length);
        matched = true;
      }
    }

    // Python keywords
    if (!matched) {
      const kwMatch = remaining.match(
        /^(\b(?:from|import|class|def|return|if|elif|else|for|while|in|not|and|or|is|with|as|try|except|finally|raise|yield|async|await|pass|break|continue|lambda|global|nonlocal|del|assert)\b)/
      );
      if (kwMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.keyword}>{kwMatch[1]}</span>);
        remaining = remaining.slice(kwMatch[1].length);
        matched = true;
      }
    }

    // Built-in values
    if (!matched) {
      const builtinMatch = remaining.match(
        /^(\b(?:True|False|None|self|print|str|int|float|dict|list|bool|set|type|len|range|super|input|open|isinstance)\b)/
      );
      if (builtinMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.builtin}>{builtinMatch[1]}</span>);
        remaining = remaining.slice(builtinMatch[1].length);
        matched = true;
      }
    }

    // Function calls
    if (!matched) {
      const funcMatch = remaining.match(/^(\w+)(\()/);
      if (funcMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.function}>{funcMatch[1]}</span>);
        tokens.push(<span key={keyIdx++} className={COLORS.punctuation}>{funcMatch[2]}</span>);
        remaining = remaining.slice(funcMatch[0].length);
        matched = true;
      }
    }

    // Named parameters (key=)
    if (!matched) {
      const paramMatch = remaining.match(/^(\w+)(=)/);
      if (paramMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.property}>{paramMatch[1]}</span>);
        tokens.push(<span key={keyIdx++} className={COLORS.operator}>{paramMatch[2]}</span>);
        remaining = remaining.slice(paramMatch[0].length);
        matched = true;
      }
    }

    // Operators
    if (!matched) {
      const opMatch = remaining.match(/^(->|=>|==|!=|<=|>=|\+=|-=|\*=|\/=|[+\-*/%<>=!&|^~])/);
      if (opMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.operator}>{opMatch[1]}</span>);
        remaining = remaining.slice(opMatch[1].length);
        matched = true;
      }
    }

    // Punctuation
    if (!matched) {
      const punctMatch = remaining.match(/^([()[\]{},.:;])/);
      if (punctMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.punctuation}>{punctMatch[1]}</span>);
        remaining = remaining.slice(1);
        matched = true;
      }
    }

    // Word tokens
    if (!matched) {
      const wordMatch = remaining.match(/^(\w+)/);
      if (wordMatch) {
        // Select a color based on length/hash to look "random" but consistent
        const flavorColors = ["text-[#61afef]", "text-[#e5c07b]", "text-[#c678dd]", "text-[#e06c75]", "text-[#56b6c2]"];
        const flavor = flavorColors[wordMatch[1].length % flavorColors.length];
        tokens.push(<span key={keyIdx++} className={flavor}>{wordMatch[1]}</span>);
        remaining = remaining.slice(wordMatch[1].length);
        matched = true;
      }
    }

    // Whitespace and other characters
    if (!matched) {
      tokens.push(<span key={keyIdx++} className={COLORS.default}>{remaining[0]}</span>);
      remaining = remaining.slice(1);
    }
  }

  return tokens;
}

function tokenizeBash(line: string): React.ReactNode[] {
  const tokens: React.ReactNode[] = [];
  let keyIdx = 0;

  // Comment lines
  if (line.trimStart().startsWith("#")) {
    return [<span key={0} className={COLORS.comment}>{line}</span>];
  }

  let remaining = line;
  while (remaining.length > 0) {
    let matched = false;

    // Prompt detector
    if (!matched) {
      const promptMatch = remaining.match(/^([$#] )/);
      if (promptMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.prompt}>{promptMatch[1]}</span>);
        remaining = remaining.slice(promptMatch[1].length);
        matched = true;
      }
    }

    // Commands
    if (!matched) {
      const cmdMatch = remaining.match(
        /^(\b(?:pip|npm|npx|git|cd|python|mtp|node|docker|curl|wget|mkdir|rm|cp|mv|cat|echo|export|source)\b)/
      );
      if (cmdMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.keyword}>{cmdMatch[1]}</span>);
        remaining = remaining.slice(cmdMatch[1].length);
        matched = true;
      }
    }

    // Flags
    if (!matched) {
      const flagMatch = remaining.match(/^(--?\w[\w-]*)/);
      if (flagMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.property}>{flagMatch[1]}</span>);
        remaining = remaining.slice(flagMatch[1].length);
        matched = true;
      }
    }

    // Strings
    if (!matched) {
      const strMatch = remaining.match(/^("[^"]*"|'[^']*')/);
      if (strMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.string}>{strMatch[1]}</span>);
        remaining = remaining.slice(strMatch[1].length);
        matched = true;
      }
    }

    // Environment vars
    if (!matched) {
      const envMatch = remaining.match(/^(\$\w+)/);
      if (envMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.variable}>{envMatch[1]}</span>);
        remaining = remaining.slice(envMatch[1].length);
        matched = true;
      }
    }

    // Default
    if (!matched) {
      const defaultMatch = remaining.match(/^(\S+)/);
      if (defaultMatch) {
        const flavorColors = ["text-[#61afef]", "text-[#e5c07b]", "text-[#c678dd]", "text-[#e06c75]", "text-[#56b6c2]"];
        const flavor = flavorColors[defaultMatch[1].length % flavorColors.length];
        tokens.push(<span key={keyIdx++} className={flavor}>{defaultMatch[1]}</span>);
        remaining = remaining.slice(defaultMatch[1].length);
        matched = true;
      }
    }

    // Whitespace
    if (!matched || (remaining.length > 0 && remaining[0] === " ")) {
      const wsMatch = remaining.match(/^(\s+)/);
      if (wsMatch) {
        tokens.push(<span key={keyIdx++}>{wsMatch[1]}</span>);
        remaining = remaining.slice(wsMatch[1].length);
      } else if (remaining.length > 0) {
        tokens.push(<span key={keyIdx++}>{remaining[0]}</span>);
        remaining = remaining.slice(1);
      }
    }
  }

  return tokens;
}

function tokenizeJson(line: string): React.ReactNode[] {
  const tokens: React.ReactNode[] = [];
  let remaining = line;
  let keyIdx = 0;

  while (remaining.length > 0) {
    let matched = false;

    // Key strings (before colon)
    if (!matched) {
      const keyMatch = remaining.match(/^("[\w$.\-_\s/]+")(\s*:)/);
      if (keyMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.key}>{keyMatch[1]}</span>);
        tokens.push(<span key={keyIdx++} className={COLORS.punctuation}>{keyMatch[2]}</span>);
        remaining = remaining.slice(keyMatch[0].length);
        matched = true;
      }
    }

    // Value strings
    if (!matched) {
      const strMatch = remaining.match(/^("[^"]*")/);
      if (strMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.string}>{strMatch[1]}</span>);
        remaining = remaining.slice(strMatch[1].length);
        matched = true;
      }
    }

    // Booleans & null
    if (!matched) {
      const boolMatch = remaining.match(/^(\b(?:true|false|null)\b)/);
      if (boolMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.boolean}>{boolMatch[1]}</span>);
        remaining = remaining.slice(boolMatch[1].length);
        matched = true;
      }
    }

    // Numbers
    if (!matched) {
      const numMatch = remaining.match(/^(\b\d+\.?\d*\b)/);
      if (numMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.number}>{numMatch[1]}</span>);
        remaining = remaining.slice(numMatch[1].length);
        matched = true;
      }
    }

    // Brackets & punctuation
    if (!matched) {
      const punctMatch = remaining.match(/^([{}[\],:])/);
      if (punctMatch) {
        tokens.push(<span key={keyIdx++} className={COLORS.punctuation}>{punctMatch[1]}</span>);
        remaining = remaining.slice(1);
        matched = true;
      }
    }

    // Default
    if (!matched) {
      tokens.push(<span key={keyIdx++} className={COLORS.default}>{remaining[0]}</span>);
      remaining = remaining.slice(1);
    }
  }

  return tokens;
}

function tokenizeEnv(line: string): React.ReactNode[] {
  const eqIdx = line.indexOf("=");
  if (eqIdx > 0) {
    return [
      <span key="k" className={COLORS.variable}>{line.slice(0, eqIdx)}</span>,
      <span key="eq" className={COLORS.operator}>=</span>,
      <span key="v" className={COLORS.string}>{line.slice(eqIdx + 1)}</span>,
    ];
  }
  if (line.trimStart().startsWith("#")) {
    return [<span key={0} className={COLORS.comment}>{line}</span>];
  }
  return [<span key={0} className={COLORS.default}>{line}</span>];
}

// ── COMPONENT ──
export function CodeBlock({ code, language = "bash", className, label, output, outputLabel }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [showOutput, setShowOutput] = useState(true);

  const highlighted = useMemo(() => highlightCode(code, language), [code, language]);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={cn("relative group rounded-lg overflow-hidden border border-white/[0.08] shadow-lg shadow-black/20", className)}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d0d0d] border-b border-white/[0.06]">
        <div className="flex items-center gap-2.5">
          <Terminal className="size-3.5 text-white/20" />
          <span className="text-[11px] font-mono text-white/35 uppercase tracking-wider">{language}</span>
          {label && (
            <>
              <span className="text-white/10">·</span>
              <span className="text-[11px] text-white/50 font-medium">{label}</span>
            </>
          )}
        </div>
        <button
          onClick={handleCopy}
          className={cn(
            "flex items-center gap-1.5 px-2 py-1 rounded text-xs transition-all",
            copied
              ? "text-emerald-400 bg-emerald-400/10"
              : "text-white/25 hover:text-white/60 hover:bg-white/[0.04]"
          )}
          aria-label="Copy code"
        >
          {copied ? (
            <>
              <Check className="size-3.5" />
              <span>Copied!</span>
            </>
          ) : (
            <>
              <Copy className="size-3.5" />
              <span className="hidden group-hover:inline">Copy</span>
            </>
          )}
        </button>
      </div>

      {/* Highlighted Code */}
      <div className="p-4 overflow-x-auto text-[13px] font-mono leading-[1.75] bg-[#050505]">
        <pre className="whitespace-pre">
          <code>{highlighted}</code>
        </pre>
      </div>

      {/* Output Section */}
      {output && (
        <>
          <button
            onClick={() => setShowOutput(!showOutput)}
            className="w-full flex items-center justify-between px-4 py-2 bg-[#080808] border-t border-white/[0.06] text-xs text-white/25 hover:text-white/45 transition-colors"
          >
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/50" />
              <span className="font-mono uppercase tracking-wider">{outputLabel || "Output"}</span>
            </div>
            {showOutput ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
          </button>
          <div
            className={cn(
              "overflow-hidden transition-all duration-200",
              showOutput ? "max-h-[500px] opacity-100" : "max-h-0 opacity-0"
            )}
          >
            <div className="px-4 py-3 bg-[#030303] border-t border-white/[0.03] text-[13px] font-mono text-emerald-400/60 leading-[1.75] overflow-x-auto">
              <pre className="whitespace-pre">
                <code>{output}</code>
              </pre>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
