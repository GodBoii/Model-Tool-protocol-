"use client";
import { motion } from "framer-motion";

const fadeUp = { hidden: { opacity: 0, y: 20 }, visible: (i = 0) => ({ opacity: 1, y: 0, transition: { duration: 0.6, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] } }) };

const PROVIDERS = [
  { name: "OpenAI",      logo: "🤖", extra: "mtpx[openai]",      model: "gpt-4o",                         color: "#10a37f" },
  { name: "Anthropic",   logo: "🧠", extra: "mtpx[anthropic]",   model: "claude-3-5-sonnet",               color: "#d97757" },
  { name: "Groq",        logo: "⚡", extra: "mtpx[groq]",        model: "llama-3.3-70b-versatile",         color: "#f55036" },
  { name: "Gemini",      logo: "✨", extra: "mtpx[gemini]",      model: "gemini-2.0-flash-exp",            color: "#4f8ef7" },
  { name: "Mistral",     logo: "🌊", extra: "mtpx[mistral]",     model: "mistral-large-latest",            color: "#ff7000" },
  { name: "Cohere",      logo: "🔗", extra: "mtpx[cohere]",      model: "command-r-plus",                  color: "#39c5bb" },
  { name: "OpenRouter",  logo: "🔀", extra: "built-in",          model: "openai/gpt-4o",                   color: "#6366f1" },
  { name: "DeepSeek",    logo: "🔭", extra: "built-in",          model: "deepseek-chat",                   color: "#00bcd4" },
  { name: "Together AI", logo: "🤝", extra: "built-in",          model: "llama-3.3-70b-instruct-turbo",    color: "#8b5cf6" },
  { name: "Fireworks AI",logo: "🎆", extra: "built-in",          model: "llama-v3p1-70b-instruct",         color: "#ec4899" },
  { name: "Ollama",      logo: "🦙", extra: "mtpx[ollama]",      model: "local models",                    color: "#84cc16" },
  { name: "LM Studio",   logo: "🖥",  extra: "mtpx[lmstudio]",   model: "local models",                    color: "#a78bfa" },
  { name: "SambaNova",   logo: "🚀", extra: "built-in",          model: "Meta-Llama-3.1-70B",              color: "#f59e0b" },
];

export function ProvidersSection() {
  return (
    <section id="providers" className="w-full max-w-7xl mx-auto px-6 py-24">
      {/* Header */}
      <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
        className="text-center mb-16">
        <p className="text-xs uppercase tracking-[0.2em] text-[#facc15] font-semibold mb-4">Universal Provider Support</p>
        <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">One SDK. Every provider.</h2>
        <p className="text-white/50 max-w-xl mx-auto text-sm leading-relaxed">
          Swap between 13+ foundation model providers without changing your agent code. Each adapter implements the same interface — deterministic, testable, interchangeable.
        </p>
      </motion.div>

      {/* Provider grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
        {PROVIDERS.map((p, i) => (
          <motion.div key={p.name} custom={i} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
            className="group relative flex items-center gap-4 p-4 rounded-xl border border-white/[0.06] transition-all duration-300 hover:border-white/[0.15] hover:-translate-y-0.5 cursor-default"
            style={{ background: "rgba(255,255,255,0.025)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}>
            {/* Subtle glow on hover */}
            <div className="absolute inset-0 rounded-xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"
              style={{ background: `radial-gradient(ellipse at 20% 50%, ${p.color}10 0%, transparent 60%)` }} />
            <div className="relative size-10 rounded-lg flex items-center justify-center text-xl flex-shrink-0 border border-white/[0.06]"
              style={{ background: `${p.color}12` }}>
              {p.logo}
            </div>
            <div className="relative min-w-0">
              <div className="font-semibold text-sm text-white/90 truncate">{p.name}</div>
              <div className="text-[11px] text-white/35 font-mono truncate">{p.model}</div>
            </div>
            {p.extra !== "built-in" && (
              <div className="relative ml-auto flex-shrink-0 text-[9px] font-mono px-1.5 py-0.5 rounded border border-white/[0.08] text-white/25">extra</div>
            )}
          </motion.div>
        ))}
      </div>

      {/* Unified API callout */}
      <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
        className="mt-10 p-5 rounded-2xl border border-white/[0.06]"
        style={{ background: "rgba(255,255,255,0.02)", backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)" }}>
        <div className="flex flex-col md:flex-row items-start md:items-center gap-4">
          <div className="flex-1">
            <div className="text-xs uppercase tracking-widest text-white/30 font-semibold mb-1">Unified Provider Interface</div>
            <div className="font-mono text-sm text-white/70">
              <span className="text-[#c678dd]">from</span>
              <span className="text-white/60"> mtp.providers </span>
              <span className="text-[#c678dd]">import</span>
              <span className="text-[#98c379]"> Groq, OpenAI, Anthropic, Gemini</span>
            </div>
          </div>
          <div className="text-xs text-white/30 md:text-right">
            <div>Same interface, any provider</div>
            <div>Zero refactoring to switch</div>
          </div>
        </div>
      </motion.div>
    </section>
  );
}
