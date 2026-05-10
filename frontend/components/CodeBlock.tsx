"use client";

import { cn } from "@/lib/utils";
import { Check, Copy } from "lucide-react";
import { useState } from "react";

interface CodeBlockProps {
  code: string;
  language?: string;
  className?: string;
}

export function CodeBlock({ code, language = "bash", className }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={cn("relative group rounded-md overflow-hidden bg-surface-container-lowest border border-white/[0.08]", className)}>
      <div className="flex items-center justify-between px-4 py-2 bg-white/[0.02] border-b border-white/[0.05]">
        <span className="text-xs font-mono text-on-surface-variant uppercase tracking-wider">{language}</span>
        <button
          onClick={handleCopy}
          suppressHydrationWarning
          className="text-on-surface-variant hover:text-primary transition-colors"
          aria-label="Copy code"
        >
          {copied ? <Check className="size-4" /> : <Copy className="size-4 opacity-50 group-hover:opacity-100" />}
        </button>
      </div>
      <div className="p-4 overflow-x-auto text-sm font-mono text-primary leading-relaxed">
        <div className="flex">
          <div className="w-1 h-full absolute left-0 top-0 bg-tertiary/20" />
          <pre>
            <code>{code}</code>
          </pre>
        </div>
      </div>
    </div>
  );
}
