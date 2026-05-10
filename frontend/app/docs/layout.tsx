"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { docSidebar } from "@/lib/docs-content";
import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { useState, useEffect } from "react";

export default function DocsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [searchQuery, setSearchQuery] = useState("");
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});

  const currentSlug = pathname.split("/docs/")[1] || "introduction";

  useEffect(() => {
    // Expand the section containing the current slug by default
    const currentSection = docSidebar.find(s => s.items.some(i => i.slug === currentSlug));
    if (currentSection) {
      setCollapsedSections(prev => ({ ...prev, [currentSection.title]: false }));
    }
  }, [currentSlug]);

  const toggleSection = (title: string) => {
    setCollapsedSections(prev => ({ ...prev, [title]: !prev[title] }));
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
      <header className="fixed top-0 left-0 right-0 z-50 h-16 border-b border-white/[0.06] bg-black/90 backdrop-blur-xl flex items-center px-6 md:px-8">
        <Link href="/" className="flex items-center gap-2.5 mr-10 group">
          <div className="relative size-10 flex-shrink-0">
            <div className="absolute inset-0 rounded-lg bg-[#facc15] opacity-0 group-hover:opacity-20 blur-md transition-opacity duration-500" />
            <Image
              src="/mtp-logo.png"
              alt="MTPX Logo"
              width={40}
              height={40}
              className="relative z-10 rounded-lg"
              priority
            />
          </div>
          <div className="flex flex-col leading-none">
            <span className="font-bold text-[20px] tracking-tight text-white">
              mtpx<span className="text-[#facc15]">.</span>
            </span>
          </div>
        </Link>

        <div className="ml-auto flex items-center gap-6">
          <Link
            href="/support"
            className="text-[15px] text-white/70 hover:text-white transition-all font-medium"
          >
            Support
          </Link>
          <a
            href="https://github.com/GodBoii/Model-Tool-protocol-"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-[15px] text-white/70 hover:text-white transition-all font-medium"
          >
            <svg viewBox="0 0 24 24" className="size-4.5 fill-current"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.228-1.552 3.335-1.23 3.335-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>
            GitHub
          </a>
        </div>
      </header>

      <div className="flex pt-16">
        {/* Sidebar */}
        <aside className="fixed top-16 left-0 bottom-0 w-72 border-r border-white/[0.06] bg-black overflow-y-auto hidden lg:block">
          {/* Search */}
          <div className="p-4 border-b border-white/[0.06]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-white/30" />
              <input
                type="text"
                placeholder="Search docs..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                suppressHydrationWarning
                className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg pl-10 pr-4 py-2 text-sm text-white placeholder:text-white/30 focus:outline-none focus:ring-1 focus:ring-[#facc15]/30 focus:border-[#facc15]/30 transition-all"
              />
            </div>
          </div>

          {/* Nav Groups */}
          <nav className="p-4 space-y-4">
            {filteredSidebar.map((section) => {
              const isCollapsed = collapsedSections[section.title] ?? false;
              return (
                <div key={section.title} className="space-y-1">
                  <button
                    onClick={() => toggleSection(section.title)}
                    suppressHydrationWarning
                    className="w-full flex items-center justify-between px-3 py-2 text-[12px] font-bold text-white/90 uppercase tracking-[0.15em] hover:text-white transition-colors group"
                  >
                    <span>{section.title}</span>
                    <ChevronDown className={`size-3.5 text-white/50 transition-transform duration-200 ${isCollapsed ? "-rotate-90" : ""}`} />
                  </button>
                  
                  {!isCollapsed && (
                    <ul className="space-y-0.5 animate-in fade-in slide-in-from-top-1 duration-200">
                      {section.items.map((item) => {
                        const isActive = currentSlug === item.slug;
                        return (
                          <li key={item.slug}>
                            <Link
                              href={`/docs/${item.slug}`}
                              className={`flex items-center gap-2 px-3 py-2 rounded-lg text-[14.5px] transition-all group ${
                                isActive
                                  ? "bg-[#facc15]/10 text-[#facc15] font-medium"
                                  : "text-white/65 hover:text-white/90 hover:bg-white/[0.03]"
                              }`}
                            >
                              {item.title}
                              {isActive && (
                                <ChevronRight className="size-3 ml-auto opacity-50" />
                              )}
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              );
            })}
          </nav>
        </aside>

        {/* Main Content */}
        <main className="flex-1 lg:ml-72 min-h-[calc(100vh-4rem)]">
          {children}
        </main>
      </div>
    </div>
  );
}
