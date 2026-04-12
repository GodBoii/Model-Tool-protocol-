"use client";

import { cn } from "@/lib/utils";
import { useParams } from "next/navigation";
import { docPages, getDocBySlug, getAllDocSlugs } from "@/lib/docs-content";
import type { DocContentBlock } from "@/lib/docs-content";
import { CodeBlock } from "@/components/CodeBlock";
import Link from "next/link";
import { ChevronLeft, ChevronRight, Info, Lightbulb, AlertTriangle } from "lucide-react";
import { useState, useEffect, useRef, useMemo, use } from "react";

// ── RIGHT-SIDE MINI NAVIGATION (CHATGPT STYLE) ──
function MiniNavigation({ sections }: { sections: { id: string; title: string; preview: string }[] }) {
  const [activeId, setActiveId] = useState("");
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting);
        if (visible.length > 0) {
          const mostVisible = visible.reduce((prev, curr) => 
            prev.boundingClientRect.top > curr.boundingClientRect.top ? curr : prev
          );
          setActiveId(mostVisible.target.id);
        }
      },
      { rootMargin: "-120px 0px -50% 0px", threshold: 0.1 }
    );

    sections.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observer.observe(el);
    });

    return () => observer.disconnect();
  }, [sections]);

  return (
    <div className="fixed right-6 top-1/2 -translate-y-1/2 z-50 hidden xl:flex flex-col items-end gap-0.5 group/nav">
      {sections.map((section) => {
        const isActive = activeId === section.id;
        const isHovered = hoveredId === section.id;

        const handleScroll = (id: string) => {
          const el = document.getElementById(id);
          if (el) {
            el.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        };

        return (
          <div 
            key={section.id}
            className="relative flex items-center justify-end h-3 pr-1"
            onMouseEnter={() => setHoveredId(section.id)}
            onMouseLeave={() => setHoveredId(null)}
          >
            {/* Hover Expansion Panel */}
            <div 
              className={cn(
                "absolute right-10 py-3 px-4 rounded-xl bg-[#0a0a0a] border border-white/[0.08] shadow-2xl transition-all duration-300 origin-right pointer-events-none w-64",
                isHovered 
                  ? "opacity-100 translate-x-0 scale-100" 
                  : "opacity-0 translate-x-4 scale-95"
              )}
            >
              <div className="text-[10px] text-tertiary font-bold uppercase tracking-widest mb-1.5 grayscale-[0.5]">Section</div>
              <div className="text-[13px] font-semibold text-white mb-1">{section.title}</div>
              <div className="text-[11px] text-white/40 line-clamp-2 leading-relaxed font-light">{section.preview}</div>
            </div>

            {/* Interaction Line */}
            <div 
              className="flex items-center group/line h-full cursor-pointer"
              onClick={() => handleScroll(section.id)}
            >
              <div 
                className={cn(
                  "h-[2px] w-6 rounded-full transition-all duration-300",
                  isActive 
                    ? "bg-tertiary shadow-[0_0_8px_rgba(255,230,0,0.3)]" 
                    : "bg-white/40 group-hover/line:bg-white"
                )}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── CONTENT RENDERER ──
function DocContentRenderer({ blocks }: { blocks: DocContentBlock[] }) {
  return (
    <div className="space-y-5">
      {blocks.map((block, idx) => {
        switch (block.type) {
          case "heading": {
            const slug = block.value.toLowerCase().replace(/[^a-z0-9]+/g, "-");
            return (
              <h2
                key={idx}
                id={slug}
                className="text-lg font-semibold tracking-tight mt-12 mb-4 text-white/90 border-b border-white/[0.04] pb-3 scroll-mt-20"
              >
                {block.value}
              </h2>
            );
          }

          case "text":
            return (
              <p key={idx} className="text-[14px] text-white/55 leading-[1.8]">
                {block.value}
              </p>
            );

          case "code":
            return (
              <div key={idx} className="my-5">
                <CodeBlock
                  code={block.value}
                  language={block.language || "bash"}
                  label={block.label}
                  output={block.output}
                  outputLabel={block.outputLabel}
                />
              </div>
            );

          case "list":
            return (
              <ul key={idx} className="space-y-2 my-4 pl-1">
                {block.items?.map((item, i) => (
                  <li key={i} className="flex items-start gap-2.5 text-[14px] text-white/55">
                    <span className="text-white/15 mt-[3px] text-[8px]">●</span>
                    <span className="leading-relaxed">{item}</span>
                  </li>
                ))}
              </ul>
            );

          case "callout": {
            const config = {
              note: { icon: Info, border: "border-tertiary/20", bg: "bg-tertiary/[0.03]", iconColor: "text-tertiary/70", label: "Note" },
              tip: { icon: Lightbulb, border: "border-tertiary/20", bg: "bg-tertiary/[0.03]", iconColor: "text-tertiary/70", label: "Tip" },
              warning: { icon: AlertTriangle, border: "border-orange-400/20", bg: "bg-orange-500/[0.03]", iconColor: "text-orange-400/70", label: "Warning" },
            }[block.calloutType || "note"]!;
            const Icon = config.icon;
            return (
              <div key={idx} className={`p-4 rounded-lg border-l-2 ${config.border} ${config.bg} my-6`}>
                <div className="flex items-center gap-2 mb-1.5">
                  <Icon className={`size-3.5 ${config.iconColor}`} />
                  <span className={`text-[10px] font-semibold uppercase tracking-wider ${config.iconColor}`}>{config.label}</span>
                </div>
                <p className="text-[13px] text-white/50 leading-relaxed">{block.value}</p>
              </div>
            );
          }

          case "table":
            return (
              <div key={idx} className="my-6 overflow-x-auto rounded-lg border border-white/[0.06]">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-white/[0.06] bg-white/[0.015]">
                      {block.headers?.map((header, hi) => (
                        <th key={hi} className="px-4 py-2.5 text-left text-[10px] font-semibold text-white/35 uppercase tracking-wider">
                          {header}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows?.map((row, ri) => (
                      <tr key={ri} className="border-b border-white/[0.03] last:border-0">
                        {row.map((cell, ci) => (
                          <td key={ci} className="px-4 py-2.5 text-white/50 font-mono text-[12px]">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );

          default:
            return null;
        }
      })}
    </div>
  );
}

// ── ADJACENT PAGE NAVIGATION ──
function getAdjacentPages(slug: string) {
  const allSlugs = getAllDocSlugs();
  const idx = allSlugs.indexOf(slug);
  const prev = idx > 0 ? getDocBySlug(allSlugs[idx - 1]) : null;
  const next = idx < allSlugs.length - 1 ? getDocBySlug(allSlugs[idx + 1]) : null;
  return { prev, next };
}

// ── MAIN PAGE ──
export default function DocPage({ params }: { params: Promise<{ slug: string }> }) {
  const resolvedParams = use(params);
  const slug = resolvedParams.slug || "introduction";
  const doc = getDocBySlug(slug);
  const content = docPages[slug];
  const { prev, next } = getAdjacentPages(slug);

  // Extract headings and previews for components
  const sections = useMemo(() => {
    if (!content) return [];
    const result: { id: string; title: string; preview: string }[] = [];
    
    content.forEach((block, idx) => {
      if (block.type === "heading") {
        const id = block.value.toLowerCase().replace(/[^a-z0-9]+/g, "-");
        // Try to find the next text block as a preview
        let preview = "Explore this section to learn more about " + block.value.toLowerCase();
        for (let i = idx + 1; i < content.length; i++) {
          if (content[i].type === "text") {
            preview = content[i].value;
            break;
          }
          if (content[i].type === "heading") break;
        }
        result.push({ id, title: block.value, preview });
      }
    });
    return result;
  }, [content]);

  const headings = useMemo(() => sections.map(s => s.title), [sections]);

  if (!doc || !content) {
    return (
      <div className="max-w-3xl mx-auto px-8 py-20 text-center">
        <h1 className="text-2xl font-bold mb-4">Page Not Found</h1>
        <p className="text-white/50 mb-8">This documentation page does not exist yet.</p>
        <Link href="/docs/introduction" className="text-tertiary hover:underline">
          ← Back to Introduction
        </Link>
      </div>
    );
  }

  return (
    <>
      <MiniNavigation sections={sections} />

      <div className="w-full max-w-6xl px-4 md:px-6 lg:px-12 py-10 pb-24 ml-0">
        {/* Breadcrumb */}
        <div className="flex items-center gap-1.5 text-[11px] text-white/20 mb-6 font-mono">
          <Link href="/docs/introduction" className="hover:text-white/40 transition-colors">docs</Link>
          <ChevronRight className="size-3" />
          <span className="text-white/35">{slug}</span>
        </div>

        {/* Page Header */}
        <header className="mb-10">
          <h1 className="text-3xl font-bold tracking-tight mb-3 text-white/95">{doc.title}</h1>
          <p className="text-[16px] text-white/45 leading-relaxed max-w-2xl">{doc.description}</p>
        </header>

        {/* Content */}
        <DocContentRenderer blocks={content} />

        {/* Navigation Footer */}
        <div className="mt-20 pt-10 border-t border-white/[0.08] flex items-center justify-between gap-6">
          {prev ? (
            <Link
              href={`/docs/${prev.slug}`}
              className="flex-1 max-w-[240px] flex items-center gap-4 p-4 rounded-xl border border-white/[0.04] bg-white/[0.01] hover:bg-white/[0.04] transition-all group"
            >
              <ChevronLeft className="size-5 text-white/20 group-hover:text-tertiary group-hover:-translate-x-1 transition-all" />
              <div>
                <div className="text-[10px] uppercase tracking-widest text-white/30 mb-1 font-bold">Previous</div>
                <div className="text-[15px] font-semibold text-white/80 group-hover:text-white transition-colors">{prev.title}</div>
              </div>
            </Link>
          ) : <div className="flex-1" />}
          
          {next ? (
            <Link
              href={`/docs/${next.slug}`}
              className="flex-1 max-w-[240px] flex items-center justify-between p-4 rounded-xl border border-white/[0.04] bg-white/[0.01] hover:bg-white/[0.04] transition-all group text-right"
            >
              <div className="flex-1">
                <div className="text-[10px] uppercase tracking-widest text-white/30 mb-1 font-bold">Next</div>
                <div className="text-[15px] font-semibold text-white/80 group-hover:text-white transition-colors">{next.title}</div>
              </div>
              <ChevronRight className="size-5 text-white/20 group-hover:text-tertiary group-hover:translate-x-1 transition-all" />
            </Link>
          ) : <div className="flex-1" />}
        </div>
      </div>
    </>
  );
}
