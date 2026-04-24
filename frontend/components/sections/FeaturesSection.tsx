"use client";
import { motion } from "framer-motion";
import { Layers, GitMerge, Bot, Shield, Database, Zap, Network, Terminal } from "lucide-react";

const fadeUp = { hidden: { opacity: 0, y: 20 }, visible: (i = 0) => ({ opacity: 1, y: 0, transition: { duration: 0.6, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] } }) };

const FEATURES = [
  { icon: GitMerge, color: "#facc15", title: "DAG Execution Plans", desc: "Models emit structured JSON execution plans. The MTP runtime resolves step dependencies via $ref before executing any tool — enabling safe parallelism and verifiable ordering." },
  { icon: Bot,      color: "#4f8ef7", title: "13+ Provider Adapters", desc: "Groq, OpenAI, Anthropic, Gemini, Mistral, Cohere, DeepSeek, OpenRouter, Ollama, LM Studio, and more. Swap without refactoring. Every adapter implements the same interface." },
  { icon: Layers,   color: "#8b5cf6", title: "Lazy Toolkit Loading", desc: "Toolkits load on-demand by prefix (e.g. github.*). Spec previews are available before handlers load, so providers discover tools without paying loading costs upfront." },
  { icon: Shield,   color: "#f43f5e", title: "Safety Policy Engine", desc: "Per-tool, per-parameter execution policies: allow, ask (human approval), or deny. Destructive operations like delete_file or shell commands get explicit guardrails." },
  { icon: Database, color: "#10b981", title: "Session Persistence", desc: "Conversation history persists across process restarts via JsonSessionStore, PostgresSessionStore, or MySQLSessionStore. Sessions keyed by session_id and user_id." },
  { icon: Zap,      color: "#22d3ee", title: "Streaming & Events", desc: "run_loop_stream() for token streaming. run_loop_events() for structured runtime events. Compatible with streaming UI and real-time tool execution dashboards." },
  { icon: Network,  color: "#f97316", title: "MCP Interoperability", desc: "Experimental JSON-RPC adapter exposes ToolRegistry as an MCP server. Supports stdio and HTTP transports with auth hooks, progress notifications, and cancellation." },
  { icon: Terminal, color: "#a78bfa", title: "Autoresearch Mode", desc: "Enable persistent execution where the agent loops autonomously until it calls agent.terminate() with a reason. Ideal for deep research and multi-step workflows." },
];

const STEPS = [
  { n: "01", title: "Instruction", desc: "User input + environment context enters the agent." },
  { n: "02", title: "Planning",    desc: "The LLM outputs a structured DAG execution plan." },
  { n: "03", title: "Validation",  desc: "MTP schema validates the plan — cycles, deps, schemas." },
  { n: "04", title: "Execution",   desc: "Runtime resolves $ref dependencies, enforces policies, runs tools." },
  { n: "05", title: "Context",     desc: "Tool results are injected back into conversation memory." },
  { n: "06", title: "Resolution",  desc: "Loop continues until the objective is complete or max_rounds hit." },
];

export function FeaturesSection() {
  return (
    <>
      {/* How It Works */}
      <section className="w-full max-w-6xl mx-auto px-6 py-24">
        <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp} className="text-center mb-16">
          <p className="text-xs uppercase tracking-[0.2em] text-[#facc15] font-semibold mb-4">Execution Model</p>
          <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">How MTP works</h2>
          <p className="text-white/50 max-w-xl mx-auto text-sm">The core difference: MTP forces the model to plan before executing. The runtime owns the actual execution — not the LLM.</p>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-16">
          {/* Steps */}
          <div className="space-y-3">
            {STEPS.map((s, i) => (
              <motion.div key={s.n} custom={i} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
                className="flex items-start gap-4 p-4 rounded-xl border border-white/[0.05] hover:border-[#facc15]/20 transition-colors group"
                style={{ background: "rgba(255,255,255,0.02)" }}>
                <div className="font-mono text-xs text-[#facc15]/50 pt-0.5 w-6 flex-shrink-0 group-hover:text-[#facc15] transition-colors">{s.n}</div>
                <div>
                  <div className="font-semibold text-sm text-white/85 mb-1">{s.title}</div>
                  <div className="text-xs text-white/40 leading-relaxed">{s.desc}</div>
                </div>
              </motion.div>
            ))}
          </div>

          {/* Execution plan viz */}
          <motion.div custom={6} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
            className="relative rounded-2xl overflow-hidden border border-white/[0.07] self-start sticky top-24"
            style={{ background: "rgba(3,3,8,0.95)", backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)", boxShadow: "0 24px 60px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05)" }}>
            <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.05]">
              <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" /><div className="w-2.5 h-2.5 rounded-full bg-[#ffbd2e]" /><div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
              <span className="ml-2 text-[11px] font-mono text-white/30">execution_plan.json</span>
            </div>
            <pre className="p-5 text-[12px] font-mono leading-relaxed overflow-x-auto text-white/60">{`{
  "plan": [
    {
      "step_id": "fetch_logs",
      "tool": "read_file",
      "args": {
        "path": "/var/log/app.log"
      }
    },
    {
      "step_id": "analyze",
      "tool": "diagnose_trace",
      "args": {
        "log_data": {
          "$ref": "fetch_logs"
        }
      },
      "depends_on": ["fetch_logs"]
    }
  ]
}`}
            </pre>
            <div className="px-5 pb-5">
              <div className="text-[10px] text-[#facc15]/50 font-mono uppercase tracking-wider">↑ $ref resolves step output as next step input</div>
            </div>
          </motion.div>
        </div>
      </section>

      {/* Core Features Grid */}
      <section className="w-full max-w-7xl mx-auto px-6 pb-24">
        <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp} className="text-center mb-16">
          <p className="text-xs uppercase tracking-[0.2em] text-[#facc15] font-semibold mb-4">Core Architecture</p>
          <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">Everything you need. Nothing you don&apos;t.</h2>
        </motion.div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {FEATURES.map((f, i) => (
            <motion.div key={f.title} custom={i} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp}
              className="group relative p-6 rounded-2xl border border-white/[0.06] hover:border-white/[0.12] transition-all duration-300 hover:-translate-y-1 cursor-default"
              style={{ background: "rgba(255,255,255,0.025)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}>
              <div className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"
                style={{ background: `radial-gradient(ellipse at 20% 20%, ${f.color}0a 0%, transparent 55%)` }} />
              <div className="relative size-10 rounded-xl mb-4 flex items-center justify-center border border-white/[0.08]"
                style={{ background: `${f.color}12` }}>
                <f.icon className="size-5" style={{ color: f.color }} />
              </div>
              <h3 className="relative font-semibold text-sm text-white/90 mb-2 leading-snug">{f.title}</h3>
              <p className="relative text-xs text-white/40 leading-relaxed">{f.desc}</p>
            </motion.div>
          ))}
        </div>
      </section>
    </>
  );
}
