"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { docPages, getDocBySlug, docSidebar, getAllDocSlugs } from "@/lib/docs-content";
import type { DocContentBlock } from "@/lib/docs-content";
import { CodeBlock } from "@/components/CodeBlock";
import Link from "next/link";
import { ChevronLeft, ChevronRight, Info, Lightbulb, AlertTriangle } from "lucide-react";
import { providers } from "@/lib/providers";
import { ProviderCard } from "@/components/docs/ProviderCard";
import { DocScrollSpy } from "@/components/docs/DocScrollSpy";


function slugify(text: string) {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-");
}

function DocContentRenderer({ blocks }: { blocks: DocContentBlock[] }) {
  const [activeTabs, setActiveTabs] = useState<Record<number, string>>({});

  return (
    <div className="space-y-6">
      {blocks.map((block, idx) => {
        switch (block.type) {
          case "heading":
            return (
              <h2 
                key={idx} 
                id={slugify(block.value || "")}
                className="text-2xl font-bold tracking-tight mt-14 mb-5 text-white/95 border-b border-white/[0.08] pb-4"
              >
                {block.value}
              </h2>
            );

          case "subheading":
            return (
              <h3 
                key={idx} 
                id={slugify(block.value || "")}
                className="text-xl font-bold tracking-tight mt-10 mb-4 text-white/85"
              >
                {block.value}
              </h3>
            );

          case "divider":
            return <hr key={idx} className="my-10 border-white/[0.06]" />;

          case "text":
            return (
              <p key={idx} className="text-[16.5px] text-white/65 leading-[1.85]">
                {block.value}
              </p>
            );

          case "code":
            return (
              <div key={idx} className="my-4">
                <CodeBlock code={block.value || ""} language={block.language || "bash"} />
              </div>
            );

          case "tabs": {
            const currentTab = activeTabs[idx] || block.tabs?.[0]?.label || "";
            const activeContent = block.tabs?.find(t => t.label === currentTab);
            return (
              <div key={idx} className="my-6 border border-white/[0.08] rounded-xl overflow-hidden bg-white/[0.01]">
                <div className="flex items-center gap-1 bg-white/[0.03] px-2 pt-2 border-b border-white/[0.06]">
                  {block.tabs?.map((tab) => (
                    <button
                      key={tab.label}
                      onClick={() => setActiveTabs(prev => ({ ...prev, [idx]: tab.label }))}
                      className={`px-4 py-2 text-xs font-medium rounded-t-lg transition-all ${
                        currentTab === tab.label 
                          ? "bg-black text-white border-x border-t border-white/[0.1] -mb-[1px]" 
                          : "text-white/40 hover:text-white/60"
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
                <div className="p-0">
                  <CodeBlock code={activeContent?.code || ""} language={activeContent?.language || "bash"} />
                </div>
              </div>
            );
          }

          case "api-method":
            return (
              <div key={idx} className="my-8 border border-white/[0.08] rounded-xl overflow-hidden bg-white/[0.01]">
                <div className="bg-white/[0.03] px-5 py-3 border-b border-white/[0.06] flex items-center justify-between">
                  <span className="font-mono text-sm font-bold text-[#facc15]">{block.value}</span>
                  <span className="text-[10px] font-bold text-white/20 uppercase tracking-widest">SDK Method</span>
                </div>
                <div className="p-5 space-y-6">
                   <div>
                     <h4 className="text-[10px] font-bold text-white/30 uppercase tracking-[0.2em] mb-3">Parameters</h4>
                     <div className="space-y-3">
                       {block.fields?.map(field => (
                         <div key={field.name} className="flex items-start gap-4 text-sm">
                           <div className="w-32 flex-shrink-0">
                             <span className="font-mono text-[#facc15]/80 font-bold">{field.name}</span>
                             {field.required && <span className="text-red-500/50 ml-1">*</span>}
                           </div>
                           <div className="w-24 flex-shrink-0 text-[11px] font-mono text-white/30">{field.type}</div>
                           <div className="text-white/50">{field.description}</div>
                         </div>
                       ))}
                     </div>
                   </div>
                   {block.returns && (
                     <div className="pt-4 border-t border-white/[0.04]">
                       <h4 className="text-[10px] font-bold text-white/30 uppercase tracking-[0.2em] mb-2">Returns</h4>
                       <div className="font-mono text-sm text-white/60">{block.returns}</div>
                     </div>
                   )}
                </div>
              </div>
            );

          case "list":
            return (
              <ul key={idx} className="space-y-3 my-4">
                {block.items?.map((item, i) => (
                  <li key={i} className="flex items-start gap-3 text-[16.5px] text-white/65">
                    <span className="text-[#facc15] mt-2 text-[10px]">●</span>
                    <span className="leading-relaxed">{item}</span>
                  </li>
                ))}
              </ul>
            );

          case "callout": {
            const config = {
              note: { icon: Info, border: "border-blue-500/30", bg: "bg-blue-500/5", iconColor: "text-blue-400", label: "Note" },
              tip: { icon: Lightbulb, border: "border-[#facc15]/30", bg: "bg-[#facc15]/5", iconColor: "text-[#facc15]", label: "Tip" },
              warning: { icon: AlertTriangle, border: "border-orange-500/30", bg: "bg-orange-500/5", iconColor: "text-orange-400", label: "Warning" },
              danger: { icon: AlertTriangle, border: "border-red-500/30", bg: "bg-red-500/5", iconColor: "text-red-400", label: "Critical" },
            }[block.calloutType || "note"]!;
            const Icon = config.icon;
            return (
              <div key={idx} className={`p-4 rounded-lg border-l-4 ${config.border} ${config.bg} my-6`}>
                <div className="flex items-center gap-2 mb-2.5">
                  <Icon className={`size-4 ${config.iconColor}`} />
                  <span className={`text-[13.5px] font-bold uppercase tracking-wider ${config.iconColor}`}>{config.label}</span>
                </div>
                <p className="text-[15.5px] text-white/75 leading-relaxed">{block.value}</p>
              </div>
            );
          }

          case "table":
            return (
              <div key={idx} className="my-6 overflow-x-auto rounded-lg border border-white/[0.08]">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-white/[0.08] bg-white/[0.02]">
                      {block.headers?.map((header, hi) => (
                        <th key={hi} className="px-4 py-3 text-left text-xs font-semibold text-white/50 uppercase tracking-wider">
                          {header}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows?.map((row, ri) => (
                      <tr key={ri} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02] transition-colors">
                        {row.map((cell, ci) => (
                          <td key={ci} className="px-4 py-3 text-white/60 font-mono text-[13px]">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );

          case "architecture":
            return (
              <div key={idx} className="my-8 bg-black border border-white/[0.08] rounded-xl p-6 font-mono text-[12px] text-[#facc15]/80 overflow-x-auto whitespace-pre leading-relaxed shadow-2xl">
                {block.value}
              </div>
            );

          case "custom-providers-grid":
            return (
              <div key={idx} className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 my-8">
                {providers.map((p) => (
                  <ProviderCard key={p.name} provider={p} />
                ))}
              </div>
            );

          default:
            return null;
        }
      })}
    </div>
  );
}

function getAdjacentPages(slug: string) {
  const allSlugs = getAllDocSlugs();
  const idx = allSlugs.indexOf(slug);
  const prev = idx > 0 ? getDocBySlug(allSlugs[idx - 1]) : null;
  const next = idx < allSlugs.length - 1 ? getDocBySlug(allSlugs[idx + 1]) : null;
  return { prev, next };
}

export default function DocPage() {
  const params = useParams();
  const slug = (params?.slug as string) || "introduction";
  const doc = getDocBySlug(slug);
  const content = docPages[slug];
  const { prev, next } = getAdjacentPages(slug);

  if (!doc || !content) {
    return (
      <div className="min-h-screen">
        <div className="max-w-5xl px-8 py-12 lg:py-20 text-center">
          <h1 className="text-2xl font-bold mb-4">Page Not Found</h1>
          <p className="text-white/50 mb-8">This documentation page does not exist yet.</p>
          <Link href="/docs/introduction" className="text-tertiary hover:underline">
            ← Back to Introduction
          </Link>
        </div>
      </div>
    );
  }

  const headings = content
    .filter(b => b.type === "heading")
    .map(b => ({
      title: b.value || "",
      id: slugify(b.value || "")
    }));

  return (
    <>
      {/* Floating right-side scroll nav */}
      <DocScrollSpy headings={headings} />

      {/* Main content */}
      <div className="w-full max-w-5xl px-8 md:px-12 lg:px-16 pt-8 pb-24">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-[13.5px] text-white/30 mb-6 font-mono">
          <Link href="/docs/introduction" className="hover:text-white/50 transition-colors">docs</Link>
          <span>/</span>
          <span className="text-white/50">{slug}</span>
        </div>

        {/* Page Header */}
        <header className="mb-10">
          <h1 className="text-4xl font-bold tracking-tight mb-4">{doc.title}</h1>
          <p className="text-xl text-white/50 leading-relaxed">{doc.description}</p>
        </header>

        {/* Content */}
        <DocContentRenderer blocks={content} />

        {/* Navigation Footer */}
        <div className="mt-20 pt-8 border-t border-white/[0.06] flex items-center justify-between">
          {prev ? (
            <Link
              href={`/docs/${prev.slug}`}
              className="flex items-center gap-2 text-sm text-white/40 hover:text-white transition-colors group"
            >
              <ChevronLeft className="size-4 group-hover:-translate-x-0.5 transition-transform" />
              <div>
                <div className="text-[10px] uppercase tracking-wider text-white/25 mb-0.5">Previous</div>
                <div>{prev.title}</div>
              </div>
            </Link>
          ) : <div />}
          {next ? (
            <Link
              href={`/docs/${next.slug}`}
              className="flex items-center gap-2 text-sm text-white/40 hover:text-white transition-colors group text-right"
            >
              <div>
                <div className="text-[10px] uppercase tracking-wider text-white/25 mb-0.5">Next</div>
                <div>{next.title}</div>
              </div>
              <ChevronRight className="size-4 group-hover:translate-x-0.5 transition-transform" />
            </Link>
          ) : <div />}
        </div>
      </div>
    </>
  );
}
