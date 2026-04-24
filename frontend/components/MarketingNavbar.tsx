"use client";

import Link from "next/link";
import { motion, useScroll, useTransform } from "framer-motion";
import { useState, useEffect } from "react";
import { Menu, X } from "lucide-react";

const navLinks = [
  { label: "Docs", href: "/docs" },
  { label: "Providers", href: "/#providers" },
  { label: "TUI", href: "/#tui" },
  { label: "GitHub", href: "https://github.com/GodBoii/Model-Tool-protocol-", external: true },
];

export function MarketingNavbar() {
  const { scrollY } = useScroll();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  const navBg = useTransform(scrollY, [0, 60], ["rgba(3,3,5,0)", "rgba(3,3,5,0.85)"]);
  const navBorder = useTransform(scrollY, [0, 60], ["rgba(255,255,255,0)", "rgba(255,255,255,0.08)"]);

  useEffect(() => {
    const unsub = scrollY.on("change", (v) => setScrolled(v > 30));
    return unsub;
  }, [scrollY]);

  return (
    <motion.nav
      style={{ background: navBg, borderColor: navBorder }}
      className="fixed top-0 left-0 right-0 z-50 border-b transition-all"
    >
      {/* backdrop blur only when scrolled */}
      <div
        className="h-[68px] flex items-center px-6 md:px-10 max-w-7xl mx-auto"
        style={{
          WebkitBackdropFilter: scrolled ? "blur(20px)" : "none",
          backdropFilter: scrolled ? "blur(20px)" : "none",
        }}
      >
        {/* Logo */}
        <Link href="/" className="flex items-center gap-3 group mr-auto">
          <div className="relative size-9 flex items-center justify-center">
            <div className="absolute inset-0 bg-[#facc15] rounded-lg opacity-20 group-hover:opacity-40 transition-opacity blur-md" />
            <div className="relative size-9 rounded-lg border border-[#facc15]/30 bg-[#facc15]/10 flex items-center justify-center font-mono font-bold text-[#facc15] text-sm tracking-tight">
              MTP
            </div>
          </div>
          <span className="font-semibold text-[17px] tracking-tight text-white">
            mtpx<span className="text-[#facc15]">.</span>
          </span>
        </Link>

        {/* Desktop links */}
        <div className="hidden md:flex items-center gap-1 mr-8">
          {navLinks.slice(0, 3).map((link) => (
            <Link
              key={link.label}
              href={link.href}
              className="px-4 py-2 rounded-full text-sm font-medium text-white/60 hover:text-white hover:bg-white/[0.05] transition-all duration-200"
            >
              {link.label}
            </Link>
          ))}
        </div>

        {/* Right CTAs */}
        <div className="hidden md:flex items-center gap-3">
          <a
            href="https://github.com/GodBoii/Model-Tool-protocol-"
            target="_blank"
            rel="noopener noreferrer"
            className="p-2 rounded-full text-white/50 hover:text-white hover:bg-white/[0.05] transition-all"
            aria-label="GitHub"
          >
            <svg viewBox="0 0 24 24" className="size-5 fill-current">
              <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.228-1.552 3.335-1.23 3.335-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
            </svg>
          </a>

          <Link
            href="/docs"
            className="relative group px-5 py-2 rounded-full bg-[#facc15] text-black text-sm font-semibold transition-all hover:scale-105 active:scale-95"
          >
            <span className="absolute inset-0 rounded-full bg-[#facc15] blur-md opacity-30 group-hover:opacity-60 transition-opacity" />
            <span className="relative">Get Started</span>
          </Link>
        </div>

        {/* Mobile toggle */}
        <button
          className="md:hidden p-2 text-white/70 hover:text-white transition-colors"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label="Toggle menu"
        >
          {mobileOpen ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </div>

      {/* Mobile menu */}
      {mobileOpen && (
        <div
          className="md:hidden border-t border-white/[0.06] px-6 py-4 flex flex-col gap-2"
          style={{
            background: "rgba(3,3,5,0.97)",
            WebkitBackdropFilter: "blur(20px)",
            backdropFilter: "blur(20px)",
          }}
        >
          {navLinks.map((link) =>
            link.external ? (
              <a
                key={link.label}
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                className="px-4 py-3 rounded-xl text-sm text-white/70 hover:text-white hover:bg-white/[0.05] transition-all"
              >
                {link.label}
              </a>
            ) : (
              <Link
                key={link.label}
                href={link.href}
                onClick={() => setMobileOpen(false)}
                className="px-4 py-3 rounded-xl text-sm text-white/70 hover:text-white hover:bg-white/[0.05] transition-all"
              >
                {link.label}
              </Link>
            )
          )}
          <Link
            href="/docs"
            onClick={() => setMobileOpen(false)}
            className="mt-2 px-4 py-3 rounded-xl bg-[#facc15] text-black text-sm font-semibold text-center hover:opacity-90 transition-opacity"
          >
            Get Started
          </Link>
        </div>
      )}
    </motion.nav>
  );
}
