"use client";
import { useState } from "react";
import { Provider } from "@/lib/providers";
import { ModelItem } from "./ModelItem";
import { ChevronDown, ChevronUp, Copy, Check } from "lucide-react";

export function ProviderCard({ provider }: { provider: Provider }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const copySnippet = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(provider.sdkSnippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div 
      className={`rounded-2xl border transition-all duration-300 overflow-hidden ${
        isExpanded ? 'border-[#facc15]/40 ring-1 ring-[#facc15]/20 shadow-[0_8px_30px_rgb(0,0,0,0.12)]' : 'border-white/[0.08] hover:border-white/20 bg-white/[0.02]'
      }`}
      style={{ background: isExpanded ? 'rgba(250,204,21,0.03)' : 'rgba(255,255,255,0.01)' }}
    >
      <div 
        className="p-5 cursor-pointer flex items-start justify-between gap-4"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-4">
          <div 
            className="size-12 rounded-xl flex items-center justify-center text-2xl border border-white/[0.08] flex-shrink-0"
            style={{ background: `${provider.color}15`, boxShadow: `inset 0 0 20px ${provider.color}10` }}
          >
            {provider.icon}
          </div>
          <div className="flex flex-col min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <h3 className="text-lg font-bold text-white/90 truncate">{provider.name}</h3>
              <span className="px-1.5 py-0.5 rounded bg-[#facc15]/10 text-[#facc15] text-[10px] font-bold uppercase tracking-wider">
                {provider.alias}
              </span>
            </div>
            <div 
              className="group/copy flex items-center gap-2 text-[11px] font-mono text-white/40 hover:text-white/60 transition-colors"
              onClick={copySnippet}
            >
              <span>{provider.sdkSnippet}</span>
              {copied ? <Check className="size-3 text-green-400" /> : <Copy className="size-3 opacity-0 group-hover/copy:opacity-100 transition-opacity" />}
            </div>
          </div>
        </div>
        
        <div className="flex flex-col items-end gap-3">
          <span className="text-[10px] font-bold uppercase tracking-widest text-white/25 bg-white/[0.05] px-2 py-0.5 rounded-full border border-white/[0.05]">
            {provider.modelCount} models
          </span>
          {isExpanded ? <ChevronUp className="size-4 text-white/30" /> : <ChevronDown className="size-4 text-white/30" />}
        </div>
      </div>

      {isExpanded && (
        <div className="border-t border-white/[0.06] bg-black/20 animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="bg-white/[0.02] px-4 py-2 text-[10px] uppercase tracking-[0.2em] font-bold text-white/20 border-b border-white/[0.04]">
            Supported Models
          </div>
          <div className="max-h-[300px] overflow-y-auto">
            {provider.models.map((model) => (
              <ModelItem key={model.name} model={model} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
