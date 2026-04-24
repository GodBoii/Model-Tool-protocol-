"use client";
import { motion } from "framer-motion";
import Image from "next/image";
import Link from "next/link";
import { Terminal, ArrowRight } from "lucide-react";

const fadeUp = { hidden: { opacity: 0, y: 20 }, visible: (i = 0) => ({ opacity: 1, y: 0, transition: { duration: 0.7, delay: i * 0.1, ease: [0.16, 1, 0.3, 1] } }) };

const COMMANDS = [
  { cmd: "/backend groq",   desc: "Switch provider mid-session" },
  { cmd: "/backend claude", desc: "Swap to Claude instantly"    },
  { cmd: "/sessions",       desc: "Browse all saved sessions"   },
  { cmd: "/new my-agent",   desc: "Start a labeled session"     },
  { cmd: "/nerdfont on",    desc: "Enable Nerd Font icons"      },
  { cmd: "/cat show",       desc: "Show animated cat companion" },
];

const FEATURES = [
  "13 provider backends — cloud & local",
  "Auto-generated session titles from first message",
  "Centralized sessions in ~/.mtp/sessions/",
  "Context window progress bar (token usage)",
  "Real-time tool event streaming display",
  "Phosphor Decay streaming text effect",
  "Animated cat companion with cursor tracking",
  "Nerd Font & emoji glyph support",
];

export function TuiSection() {
  return (
    <section id="tui" className="w-full max-w-7xl mx-auto px-6 py-24">
      <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp} className="text-center mb-16">
        <p className="text-xs uppercase tracking-[0.2em] text-[#facc15] font-semibold mb-4">Interactive Terminal UI</p>
        <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">
          A terminal interface that doesn&apos;t compromise.
        </h2>
        <p className="text-white/50 max-w-2xl mx-auto text-sm leading-relaxed">
          <code className="font-mono text-[#facc15]">mtp tui</code> launches a premium terminal chat experience — switch between 13 AI providers, manage persistent sessions, stream tool events in real-time, and track context window usage.
        </p>
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 items-center">
        {/* Left: TUI Screenshot */}
        <motion.div custom={0} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
          className="relative group">
          {/* Glow behind image */}
          <div className="absolute -inset-4 rounded-3xl opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none"
            style={{ background: "radial-gradient(ellipse, rgba(250,204,21,0.06) 0%, transparent 70%)" }} />

          {/* Terminal chrome */}
          <div className="relative rounded-2xl overflow-hidden border border-white/[0.08] shadow-2xl"
            style={{ background: "rgba(3,3,8,0.95)", backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)", boxShadow: "0 32px 80px rgba(0,0,0,0.7), inset 0 1px 0 rgba(255,255,255,0.05)" }}>
            {/* Titlebar */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.05]" style={{ background: "rgba(255,255,255,0.02)" }}>
              <div className="w-3 h-3 rounded-full bg-[#ff5f57]" />
              <div className="w-3 h-3 rounded-full bg-[#ffbd2e]" />
              <div className="w-3 h-3 rounded-full bg-[#28c840]" />
              <div className="ml-3 text-xs font-mono text-white/25 flex items-center gap-2">
                <Terminal className="size-3" /> mtp tui
              </div>
              <div className="ml-auto text-[10px] font-mono text-[#facc15]/50">groq · llama-3.3-70b</div>
            </div>
            {/* Screenshot */}
            <div className="relative">
              <Image
                src="/assets/mtp-tui-homepage.png"
                alt="MTP TUI — Interactive Terminal Interface"
                width={800}
                height={500}
                className="w-full h-auto object-cover"
                style={{ maxHeight: "420px", objectFit: "cover", objectPosition: "top" }}
                priority
              />
              {/* Subtle gradient overlay at bottom */}
              <div className="absolute bottom-0 left-0 right-0 h-16 pointer-events-none"
                style={{ background: "linear-gradient(transparent, rgba(3,3,8,0.8))" }} />
            </div>
          </div>
        </motion.div>

        {/* Right: Features */}
        <div className="flex flex-col gap-8">
          {/* Quick commands */}
          <motion.div custom={1} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}>
            <div className="text-xs uppercase tracking-widest text-white/30 font-semibold mb-4">TUI Commands</div>
            <div className="space-y-2">
              {COMMANDS.map((c) => (
                <div key={c.cmd} className="flex items-center gap-3 group/cmd">
                  <code className="text-sm font-mono text-[#facc15]/80 group-hover/cmd:text-[#facc15] transition-colors">{c.cmd}</code>
                  <span className="text-white/20">—</span>
                  <span className="text-sm text-white/40 group-hover/cmd:text-white/60 transition-colors">{c.desc}</span>
                </div>
              ))}
            </div>
          </motion.div>

          {/* Feature list */}
          <motion.div custom={2} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
            className="p-5 rounded-2xl border border-white/[0.06]"
            style={{ background: "rgba(255,255,255,0.02)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}>
            <div className="text-xs uppercase tracking-widest text-white/30 font-semibold mb-4">Capabilities</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {FEATURES.map((f) => (
                <div key={f} className="flex items-start gap-2 text-sm text-white/50">
                  <span className="text-[#facc15] mt-0.5 flex-shrink-0">✓</span>
                  <span>{f}</span>
                </div>
              ))}
            </div>
          </motion.div>

          {/* Launch CTA */}
          <motion.div custom={3} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}>
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3 p-4 rounded-xl border border-white/[0.06] font-mono text-sm"
                style={{ background: "rgba(255,255,255,0.025)" }}>
                <span className="text-[#facc15]">$</span>
                <span className="text-white/70">pip install mtpx &amp;&amp; mtp tui</span>
              </div>
              <Link href="/docs/tui"
                className="inline-flex items-center gap-2 text-sm text-[#facc15]/70 hover:text-[#facc15] transition-colors">
                Read TUI documentation <ArrowRight className="size-3.5" />
              </Link>
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
