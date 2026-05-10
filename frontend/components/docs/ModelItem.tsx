"use client";
import { Model } from "@/lib/providers";

export function ModelItem({ model }: { model: Model }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between py-3 px-4 border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02] transition-colors gap-2">
      <div className="flex flex-col">
        <span className="text-sm font-mono text-white/80">{model.name}</span>
        <span className="text-[10px] text-white/30 uppercase tracking-wider">Context: {model.contextWindow}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {model.capabilities.map((cap) => (
          <span key={cap} className="text-[9px] px-1.5 py-0.5 rounded bg-white/[0.05] border border-white/[0.08] text-white/40 font-medium uppercase tracking-tighter">
            {cap}
          </span>
        ))}
      </div>
    </div>
  );
}
