"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Search } from "lucide-react";

const sidebarGroups = [
  {
    title: "Overview",
    links: [
      { name: "Home", href: "/" },
      { name: "Dashboard", href: "/dashboard" },
    ],
  },
  {
    title: "Visualizers",
    links: [
      { name: "Execution", href: "/execution" },
      { name: "Playground", href: "/playground" },
    ],
  },
  {
    title: "Documentation",
    links: [
      { name: "Quickstart", href: "/docs" },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-[260px] h-[calc(100vh-64px)] flex-shrink-0 flex flex-col border-r border-white/[0.08] bg-surface overflow-y-auto sticky top-16">
      <div className="p-4 border-b border-white/[0.05]">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-on-surface-variant opacity-70" />
          <input 
            type="text" 
            placeholder="Search..." 
            className="w-full bg-surface-container border border-white/[0.1] rounded-md pl-9 pr-3 py-1.5 text-sm focus:outline-none focus:border-tertiary text-on-surface placeholder:text-on-surface-variant/70"
          />
          <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center">
            <kbd className="hidden sm:inline-block border border-white/20 bg-surface-container-high rounded px-1.5 text-[10px] font-mono text-on-surface-variant">⌘K</kbd>
          </div>
        </div>
      </div>

      <div className="flex-1 py-4 px-3 space-y-6">
        {sidebarGroups.map((group) => (
          <div key={group.title}>
            <h3 className="px-3 mb-2 text-xs font-semibold text-on-surface-variant/80 tracking-wide">
              {group.title}
            </h3>
            <ul className="space-y-[2px]">
              {group.links.map((link) => {
                const isActive = pathname === link.href;
                return (
                  <li key={link.href}>
                    <Link
                      href={link.href}
                      className={cn(
                        "block px-3 py-1.5 text-sm rounded-md transition-colors",
                        isActive
                          ? "bg-surface-container-highest text-on-surface font-medium"
                          : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                      )}
                    >
                      {link.name}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </aside>
  );
}
