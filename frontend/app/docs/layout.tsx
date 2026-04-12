"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { docSidebar } from "@/lib/docs-content";
import { Hexagon, ChevronDown, ChevronRight, Search, ExternalLink, Play } from "lucide-react";
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
        <Link href="/" className="flex items-center gap-3 mr-10 group">
          <Hexagon className="size-6 text-tertiary fill-tertiary/10" />
          <span className="font-bold text-[18px] tracking-tight text-white">MTP Protocol</span>
        </Link>

        <div className="ml-auto flex items-center gap-6">
          <Link
            href="/support"
            className="text-[14px] text-white/70 hover:text-white transition-all font-medium"
          >
            Support
          </Link>
          <a
            href="https://github.com/GodBoii/Model-Tool-protocol-"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-[14px] text-white/70 hover:text-white transition-all font-medium"
          >
            <svg viewBox="0 0 24 24" className="size-4.5 fill-current"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.228-1.552 3.335-1.23 3.335-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>
            GitHub
          </a>
          <Link
            href="/playground"
            className="px-5 py-1.5 rounded-full bg-tertiary text-black font-bold text-[14px] hover:scale-[1.02] active:scale-[0.98] transition-all flex items-center gap-1.5"
          >
            Try AgentOS
            <ChevronRight className="size-3.5" />
          </Link>
        </div>

      </header>

      <div className="flex pt-16">
        {/* ── LEFT SIDEBAR ── */}
        <aside className="fixed top-16 left-0 bottom-0 w-64 border-r border-white/[0.06] bg-black overflow-y-auto hide-scrollbar hidden lg:block">

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
