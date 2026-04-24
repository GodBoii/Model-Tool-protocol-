"use client";
import { motion } from "framer-motion";
import Link from "next/link";
import { ArrowRight, Terminal } from "lucide-react";

const fadeUp = { hidden: { opacity: 0, y: 20 }, visible: (i = 0) => ({ opacity: 1, y: 0, transition: { duration: 0.6, delay: i * 0.1, ease: [0.16, 1, 0.3, 1] } }) };

const VS = [
  { old: "Probabilistic text generation", next: "Deterministic task execution" },
  { old: "Unstructured sequential tool calls", next: "DAG-based dependency resolution" },
  { old: "Ephemeral in-memory state", next: "Persistent JSON/Postgres/MySQL sessions" },
  { old: "Black-box LLM trust", next: "Policy-driven allow/ask/deny controls" },
  { old: "Single provider lock-in", next: "Swap 13+ providers with one line" },
  { old: "No execution visibility", next: "Real-time structured event streaming" },
];

const QUICKSTART = `from mtp import Agent
from mtp.providers import Groq
from mtp.toolkits import (
    CalculatorToolkit,
    FileToolkit,
    ShellToolkit,
)

Agent.load_dotenv_if_available()

tools = Agent.ToolRegistry()
tools.register_toolkit_loader("calc",  CalculatorToolkit())
tools.register_toolkit_loader("file",  FileToolkit(base_dir="."))
tools.register_toolkit_loader("shell", ShellToolkit(base_dir="."))

agent = Agent.MTPAgent(
    provider=Groq(model="llama-3.3-70b-versatile"),
    tools=tools,
    instructions="Use tools when needed.",
    strict_dependency_mode=True,
)

agent.print_response(
    "Calculate 25*4+10, then list files.",
    max_rounds=4,
    stream=True,
    stream_events=True,
)`;

export function CtaSection() {
  return (
    <>
      {/* VS comparison */}
      <section className="w-full max-w-5xl mx-auto px-6 py-24">
        <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp} className="text-center mb-12">
          <p className="text-xs uppercase tracking-[0.2em] text-[#facc15] font-semibold mb-4">Why MTP</p>
          <h2 className="text-3xl md:text-4xl font-bold tracking-tight">Traditional AI vs. MTP</h2>
        </motion.div>
        <div className="space-y-2">
          {VS.map((v, i) => (
            <motion.div key={i} custom={i} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
              className="flex items-center gap-4 p-4 rounded-xl border border-white/[0.05] hover:border-white/[0.10] transition-all group"
              style={{ background: "rgba(255,255,255,0.02)" }}>
              <span className="flex-1 text-sm text-white/30 line-through decoration-white/15">{v.old}</span>
              <ArrowRight className="size-4 text-[#facc15]/50 flex-shrink-0 group-hover:text-[#facc15] transition-colors" />
              <span className="flex-1 text-sm font-medium text-white/80">{v.next}</span>
            </motion.div>
          ))}
        </div>
      </section>

      {/* Quickstart code */}
      <section className="w-full max-w-4xl mx-auto px-6 pb-24">
        <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp} className="text-center mb-10">
          <p className="text-xs uppercase tracking-[0.2em] text-[#facc15] font-semibold mb-4">Quickstart</p>
          <h2 className="text-3xl font-bold tracking-tight mb-3">Production agent in 30 lines.</h2>
          <p className="text-white/45 text-sm">No scaffolding. No magic. Just Python.</p>
        </motion.div>

        <motion.div custom={1} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
          className="relative rounded-2xl overflow-hidden border border-white/[0.07]"
          style={{ background: "rgba(3,3,8,0.97)", backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)", boxShadow: "0 24px 60px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05)" }}>
          <div className="flex items-center justify-between px-5 py-3 border-b border-white/[0.05]">
            <div className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" /><div className="w-2.5 h-2.5 rounded-full bg-[#ffbd2e]" /><div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
            </div>
            <span className="text-[11px] font-mono text-white/25">agent.py</span>
            <span className="text-[11px] font-mono text-white/20">python</span>
          </div>
          <pre className="p-6 text-[12.5px] font-mono leading-[1.8] overflow-x-auto">
            <code className="text-white/65">{QUICKSTART}</code>
          </pre>
        </motion.div>

        <motion.div custom={2} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp} className="mt-6 flex flex-col sm:flex-row gap-4 justify-center">
          <Link href="/docs" className="relative group inline-flex items-center justify-center gap-2 px-8 py-3.5 rounded-full bg-[#facc15] text-black font-semibold text-sm hover:scale-105 active:scale-95 transition-transform">
            <span className="absolute inset-0 rounded-full bg-[#facc15] blur-lg opacity-25 group-hover:opacity-50 transition-opacity" />
            <span className="relative z-10 flex items-center gap-2"><Terminal className="size-4" /> Read the Docs</span>
          </Link>
          <a href="https://github.com/GodBoii/Model-Tool-protocol-" target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center justify-center gap-2 px-8 py-3.5 rounded-full border border-white/10 text-white/70 text-sm font-medium hover:bg-white/[0.05] hover:text-white transition-all"
            style={{ backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}>
            <svg viewBox="0 0 24 24" className="size-4 fill-current"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.228-1.552 3.335-1.23 3.335-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" /></svg>
            View on GitHub
          </a>
        </motion.div>
      </section>

      {/* Footer CTA */}
      <section className="w-full max-w-4xl mx-auto px-6 py-24 border-t border-white/[0.04]">
        <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp} className="text-center">
          <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-5">
            Stop building chatbots.<br />
            <span style={{ background: "linear-gradient(135deg,#facc15,#f97316)", WebkitBackgroundClip: "text", backgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              Start shipping execution pipelines.
            </span>
          </h2>
          <p className="text-white/40 mb-10 max-w-xl mx-auto text-sm leading-relaxed">
            MTPX gives language models structured agency — not just text generation. Protocol-first, production-ready, MIT licensed.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link href="/docs" className="relative group inline-flex items-center gap-2 px-8 py-4 rounded-full bg-white text-black font-semibold text-sm hover:scale-105 active:scale-95 transition-transform">
              <span className="absolute inset-0 rounded-full bg-white blur-xl opacity-20 group-hover:opacity-40 transition-opacity" />
              <span className="relative z-10 flex items-center gap-2">Get Started <ArrowRight className="size-4" /></span>
            </Link>
            <Link href="/docs/introduction"
              className="inline-flex items-center gap-2 px-8 py-4 rounded-full border border-white/10 text-white/70 text-sm font-medium hover:bg-white/[0.05] hover:text-white transition-all"
              style={{ backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}>
              Read Introduction
            </Link>
          </div>
        </motion.div>
      </section>

      {/* Footer */}
      <footer className="w-full border-t border-white/[0.04] py-8 px-6">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4 text-xs text-white/25">
          <div className="flex items-center gap-2 font-mono">
            <span className="text-[#facc15]/60">mtpx</span> · MIT License · Made by Prajwal Ghadge
          </div>
          <div className="flex items-center gap-6">
            <Link href="/docs" className="hover:text-white/50 transition-colors">Docs</Link>
            <a href="https://pypi.org/project/mtpx/" target="_blank" rel="noopener noreferrer" className="hover:text-white/50 transition-colors">PyPI</a>
            <a href="https://github.com/GodBoii/Model-Tool-protocol-" target="_blank" rel="noopener noreferrer" className="hover:text-white/50 transition-colors">GitHub</a>
          </div>
        </div>
      </footer>
    </>
  );
}
