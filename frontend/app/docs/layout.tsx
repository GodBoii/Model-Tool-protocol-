"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { docSidebar } from "@/lib/docs-content";
import { Book, ChevronDown, ChevronRight, Search, ExternalLink, Play } from "lucide-react";
import { useState, useEffect } from "react";

export default function DocsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [searchQuery, setSearchQuery] = useState("");
  const [openSections, setOpenSections] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    docSidebar.forEach((s) => { initial[s.title] = true; });
    return initial;
  });

  const currentSlug = pathname.split("/docs/")[1] || "introduction";


  const toggleSection = (title: string) => {
    setOpenSections((prev) => ({ ...prev, [title]: !prev[title] }));
  };

  const filteredSidebar = docSidebar
    .map((section) => ({
      ...section,
      items: section.items.filter(
        (item) =>
          !searchQuery ||
          item.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
          item.description.toLowerCase().includes(searchQuery.toLowerCase())
      ),
    }))
    .filter((section) => section.items.length > 0);

  return (
    <div className="min-h-screen bg-black text-white">
      {/* ── TOP HEADER ── */}
      <header className="fixed top-0 left-0 right-0 z-50 h-16 border-b border-white/[0.06] bg-black/90 backdrop-blur-xl flex items-center px-8">
        <Link href="/" className="flex items-center gap-3 mr-10">
          <Book className="size-6 text-tertiary" />
          <span className="font-semibold text-[17px] tracking-tight">MTP</span>
        </Link>

        <nav className="flex items-center gap-2 text-[15px]">
          <Link
            href="/docs/introduction"
            className="px-3 py-1.5 rounded-md text-white/70 hover:text-white hover:bg-white/[0.04] transition-all"
          >
            Docs
          </Link>
          <Link
            href="/playground"
            className="px-3 py-1.5 rounded-md text-white/40 hover:text-white hover:bg-white/[0.04] transition-all flex items-center gap-1.5"
          >
            <Play className="size-4" />
            Playground
          </Link>
          <a
            href="https://github.com/GodBoii/Model-Tool-protocol-"
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1.5 rounded-md text-white/40 hover:text-white hover:bg-white/[0.04] transition-all flex items-center gap-1.5"
          >
            <ExternalLink className="size-4.5" />
            GitHub
          </a>
        </nav>

      </header>

      <div className="flex pt-16">
        {/* ── LEFT SIDEBAR ── */}
        <aside className="fixed top-16 left-0 bottom-0 w-64 border-r border-white/[0.06] bg-black overflow-y-auto hide-scrollbar hidden lg:block">
          {/* Sidebar Search */}
          <div className="p-3 border-b border-white/[0.04]">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-white/20" />
              <input
                type="text"
                placeholder="Filter pages..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-white/[0.03] border border-white/[0.06] rounded-md pl-8 pr-3 py-1.5 text-xs text-white placeholder:text-white/25 focus:outline-none focus:border-white/[0.12] transition-all"
              />
            </div>
          </div>

          {/* Nav Groups — Collapsible Accordion */}
          <nav className="py-2">
            {filteredSidebar.map((section) => {
              const isOpen = openSections[section.title] ?? false;
              const hasActive = section.items.some((i) => i.slug === currentSlug);

              return (
                <div key={section.title} className="mb-0.5">
                  <button
                    onClick={() => toggleSection(section.title)}
                    className={`w-full flex items-center justify-between px-4 py-2 text-left group transition-colors ${
                      hasActive ? "text-white/70" : "text-white/40 hover:text-white/60"
                    }`}
                  >
                    <span className="text-[11px] font-semibold uppercase tracking-[0.1em]">
                      {section.title}
                    </span>
                    <ChevronDown
                      className={`size-3 transition-transform duration-200 ${
                        isOpen ? "rotate-0" : "-rotate-90"
                      }`}
                    />
                  </button>

                  {/* Collapsible Content */}
                  <div
                    className={`overflow-hidden transition-all duration-200 ease-out ${
                      isOpen ? "max-h-[500px] opacity-100" : "max-h-0 opacity-0"
                    }`}
                  >
                    <ul className="pb-2">
                      {section.items.map((item) => {
                        const isActive = currentSlug === item.slug;
                        return (
                          <li key={item.slug}>
                            <Link
                              href={`/docs/${item.slug}`}
                              className={`flex items-center gap-2 pl-7 pr-4 py-[6px] text-[13px] transition-all ${
                                isActive
                                  ? "text-white bg-white/[0.06] border-l-2 border-tertiary"
                                  : "text-white/40 hover:text-white/70 hover:bg-white/[0.02] border-l-2 border-transparent"
                              }`}
                            >
                              {item.title}
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                </div>
              );
            })}
          </nav>
        </aside>

        {/* ── MAIN CONTENT ── */}
        <main className="flex-1 lg:ml-64 min-h-[calc(100vh-3.5rem)]">
          {children}
        </main>
      </div>

      {/* ── FLOATING ASK AI BUTTON ── */}
      <div className="fixed bottom-6 right-6 z-[60]">
        <button className="flex items-center gap-2.5 px-4 py-2.5 rounded-full bg-tertiary text-black font-semibold text-[13px] shadow-2xl shadow-tertiary/20 hover:scale-105 active:scale-95 transition-all group">
          <div className="size-4 bg-black/10 rounded-full flex items-center justify-center group-hover:bg-black/20 transition-colors">
            <span className="text-[10px]">✨</span>
          </div>
          Ask AI
          <kbd className="hidden md:flex ml-1 px-1.5 py-0.5 rounded border border-black/10 bg-black/5 text-[9px] font-mono opacity-50">
            /
          </kbd>
        </button>
      </div>
    </div>
  );
}
