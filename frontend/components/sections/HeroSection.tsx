"use client";
import { motion } from "framer-motion";
import Link from "next/link";
import { Terminal, ArrowRight, Star } from "lucide-react";
import { useState } from "react";

const fadeUp = { hidden: { opacity: 0, y: 28 }, visible: (i = 0) => ({ opacity: 1, y: 0, transition: { duration: 0.7, delay: i * 0.12, ease: [0.16, 1, 0.3, 1] } }) };

export function HeroSection() {
  const [copied, setCopied] = useState(false);
  const copy = () => { navigator.clipboard.writeText("pip install mtpx"); setCopied(true); setTimeout(() => setCopied(false), 2000); };

  return (
    <section className="relative w-full min-h-screen flex flex-col items-center justify-center text-center px-6 pt-24 pb-16 overflow-hidden">
      {/* Glow blobs behind glass */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[700px] rounded-full pointer-events-none" style={{ background: "radial-gradient(ellipse, rgba(250,204,21,0.07) 0%, transparent 65%)" }} />
      <div className="absolute top-1/3 left-1/4 w-[400px] h-[400px] rounded-full pointer-events-none" style={{ background: "radial-gradient(ellipse, rgba(79,142,247,0.08) 0%, transparent 65%)" }} />

      {/* Badge */}
      <motion.div custom={0} initial="hidden" animate="visible" variants={fadeUp} className="mb-8">
        <a href="https://pypi.org/project/mtpx/" target="_blank" rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-[#facc15]/25 text-[#facc15] text-xs font-medium tracking-wide"
          style={{ background: "rgba(250,204,21,0.06)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}>
          <Star className="size-3 fill-current" /> v0.1.17 on PyPI — protocol-first agent orchestration
        </a>
      </motion.div>

      {/* H1 */}
      <motion.h1 custom={1} initial="hidden" animate="visible" variants={fadeUp}
        className="text-[3.2rem] md:text-[5rem] lg:text-[6.5rem] font-bold tracking-[-0.04em] leading-[1.05] max-w-5xl mb-6">
        <span className="text-white">Models reason.</span><br />
        <span style={{ background: "linear-gradient(135deg,#facc15 0%,#f97316 55%,#facc15 100%)", backgroundSize: "200%", WebkitBackgroundClip: "text", backgroundClip: "text", WebkitTextFillColor: "transparent" }}>
          MTP executes.
        </span>
      </motion.h1>

      {/* Sub */}
      <motion.p custom={2} initial="hidden" animate="visible" variants={fadeUp}
        className="text-base md:text-xl text-white/55 max-w-2xl mb-12 leading-relaxed font-light">
        Protocol-first Python SDK for AI agent tool orchestration. Structured DAG execution plans, 13+ provider adapters, safety policies, and persistent sessions — production-ready from day one.
      </motion.p>

      {/* CTAs */}
      <motion.div custom={3} initial="hidden" animate="visible" variants={fadeUp} className="flex flex-col sm:flex-row items-center gap-4 mb-12">
        <Link href="/docs" className="relative group inline-flex items-center gap-2 px-7 py-3.5 rounded-full bg-[#facc15] text-black font-semibold text-sm hover:scale-105 active:scale-95 transition-transform">
          <span className="absolute inset-0 rounded-full bg-[#facc15] blur-lg opacity-30 group-hover:opacity-60 transition-opacity" />
          <Terminal className="size-4 relative z-10" />
          <span className="relative z-10">Get Started</span>
        </Link>
        <a href="#tui" className="inline-flex items-center gap-2 px-7 py-3.5 rounded-full border border-white/10 text-white/80 text-sm font-medium hover:bg-white/[0.05] hover:border-white/20 transition-all hover:scale-105"
          style={{ backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}>
          See TUI Demo <ArrowRight className="size-4" />
        </a>
      </motion.div>

      {/* Install pill */}
      <motion.button custom={4} initial="hidden" animate="visible" variants={fadeUp}
        onClick={copy}
        className="group flex items-center gap-3 px-5 py-3 rounded-xl border border-white/10 text-sm font-mono hover:border-white/20 transition-all"
        style={{ background: "rgba(255,255,255,0.03)", backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)" }}>
        <span className="text-[#facc15]">$</span>
        <span className="text-white/70">pip install mtpx</span>
        <span className="text-xs text-white/30 group-hover:text-[#facc15] transition-colors ml-2">{copied ? "✓ copied" : "copy"}</span>
      </motion.button>

      {/* Social proof */}
      <motion.div custom={5} initial="hidden" animate="visible" variants={fadeUp} className="mt-10 flex flex-wrap items-center justify-center gap-6 text-xs text-white/25">
        {["13+ Providers", "OpenAI · Anthropic · Groq · Gemini", "MCP Compatible", "MIT License"].map(t => (
          <span key={t} className="flex items-center gap-1.5"><span className="size-1 rounded-full bg-white/20 inline-block" />{t}</span>
        ))}
      </motion.div>
    </section>
  );
}
