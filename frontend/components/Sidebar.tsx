"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

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
      { name: "Introduction", href: "/docs/introduction" },
      { name: "Quickstart", href: "/docs/quickstart" },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 h-screen flex-shrink-0 flex flex-col border-r border-white/[0.06] bg-black overflow-y-auto sticky top-0 pt-16 hide-scrollbar">
      <div className="flex-1 py-10 px-4 space-y-8">
        {sidebarGroups.map((group) => (
          <div key={group.title}>
            <h3 className="px-3 mb-4 text-[11px] font-bold text-white/40 uppercase tracking-widest">
              {group.title}
            </h3>
            <ul className="space-y-1">
              {group.links.map((link) => {
                const isActive = pathname === link.href;
                return (
                  <li key={link.href}>
                    <Link
                      href={link.href}
                      className={cn(
                        "flex items-center px-4 py-2 text-[14px] leading-tight transition-all rounded-md",
                        isActive
                          ? "text-white bg-white/[0.05] border-l-2 border-tertiary font-medium"
                          : "text-white/40 hover:text-white/70 hover:bg-white/[0.02] border-l-2 border-transparent"
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
