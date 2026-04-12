import { AnimatedCard } from "@/components/AnimatedCard";
import { PlaySquare, Send, Server, User, Cpu } from "lucide-react";

export default function Playground() {
  return (
    <div className="min-h-screen bg-black flex flex-col text-white/90">
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-12 lg:p-16 flex gap-12">
        
        {/* Sidebar settings */}
        <aside className="w-64 flex-shrink-0 flex flex-col gap-10">
          <div>
            <h2 className="text-[11px] font-bold tracking-[0.2em] text-white/40 uppercase mb-6">Configuration</h2>
            <div className="space-y-6">
              <div className="flex flex-col gap-2">
                <label className="text-[11px] font-medium text-white/30 uppercase tracking-widest">Provider</label>
                <select className="bg-white/[0.03] border border-white/[0.06] rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:border-tertiary transition-all">
                  <option className="bg-black">Groq</option>
                  <option className="bg-black">OpenRouter</option>
                  <option className="bg-black">Anthropic</option>
                </select>
              </div>

              <div className="flex flex-col gap-2">
                <label className="text-[11px] font-medium text-white/30 uppercase tracking-widest">Model</label>
                <select className="bg-white/[0.03] border border-white/[0.06] rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:border-tertiary transition-all">
                  <option className="bg-black">Llama 3 8B (Groq)</option>
                  <option className="bg-black">Claude 3.5 Sonnet</option>
                </select>
              </div>
            </div>
          </div>
          
          <div>
            <h2 className="text-[11px] font-bold tracking-[0.2em] text-white/40 uppercase mb-6">Tools</h2>
            <div className="space-y-3">
              {['search_web', 'read_file', 'run_command', 'get_weather'].map((tool) => (
                <label key={tool} className="flex items-center gap-3 text-[13px] text-white/50 hover:text-white/80 cursor-pointer transition-all">
                  <input type="checkbox" defaultChecked className="accent-tertiary size-4 rounded bg-white/[0.05] border-white/10" />
                  {tool}
                </label>
              ))}
            </div>
          </div>
        </aside>

        {/* Chat / Playground Area */}
        <section className="flex-1 flex flex-col min-h-0 bg-white/[0.02] rounded-2xl border border-white/[0.06] overflow-hidden shadow-2xl">
          <div className="flex-1 overflow-y-auto p-8 space-y-8 hide-scrollbar">
            
            <div className="flex gap-5">
              <div className="size-9 rounded-xl bg-white/[0.05] flex items-center justify-center border border-white/[0.1] flex-shrink-0">
                <User className="size-4.5 text-white/60" />
              </div>
              <div className="flex-1">
                <p className="text-[15px] text-white/80 leading-relaxed mt-1.5">
                  Can you check the current weather in Tokyo and then run a quick search for top events happening there this weekend?
                </p>
              </div>
            </div>

            <div className="flex gap-5">
              <div className="size-9 rounded-xl bg-tertiary/10 flex items-center justify-center border border-tertiary/20 flex-shrink-0">
                <Cpu className="size-4.5 text-tertiary" />
              </div>
              <div className="flex-1 space-y-6">
                <p className="text-[15px] text-white/80 leading-relaxed mt-1.5">
                  I will use my tools to gather that information for you.
                </p>
                
                <div className="p-4 rounded-xl border border-tertiary/20 bg-tertiary/[0.03] flex flex-col gap-3">
                  <div className="flex items-center gap-2 text-[11px] font-bold text-tertiary uppercase tracking-widest">
                    <Server className="size-3.5" />
                    <span>Tool Call: get_weather</span>
                  </div>
                  <pre className="text-[13px] text-white/60 font-mono bg-black/40 p-3 rounded-lg border border-white/[0.05]">
                    {`{"location": "Tokyo", "unit": "celsius"}`}
                  </pre>
                </div>
                
                <div className="p-4 rounded-xl border border-white/[0.06] bg-white/[0.01] flex flex-col gap-3">
                   <div className="flex items-center gap-2 text-[11px] font-bold text-white/30 uppercase tracking-widest">
                    <Server className="size-3.5" />
                    <span>Result</span>
                  </div>
                  <p className="text-[13px] text-white/60 font-mono">
                    22°C, mostly cloudy.
                  </p>
                </div>

                <p className="text-[15px] text-white/80 leading-relaxed">
                  Currently, it is 22°C and mostly cloudy in Tokyo. Now, I will search for the events...
                </p>
              </div>
            </div>

          </div>

          <div className="p-6 bg-black/40 border-t border-white/[0.06]">
            <div className="relative flex items-center group">
              <textarea 
                className="w-full bg-white/[0.03] border border-white/[0.08] rounded-xl pl-5 pr-14 py-4 text-[14px] text-white placeholder:text-white/20 focus:outline-none focus:border-tertiary/50 transition-all resize-none shadow-inner"
                placeholder="Message MTP Agent..."
                rows={1}
              />
              <button className="absolute right-3 size-10 bg-tertiary text-black rounded-lg hover:scale-105 active:scale-95 transition-all flex items-center justify-center shadow-lg shadow-tertiary/20">
                <Send className="size-4.5" />
              </button>
            </div>
            <p className="text-center text-[11px] text-white/20 mt-4 tracking-wide">
              MTP Agents can make mistakes. Consider verifying important information.
            </p>
          </div>
        </section>

      </main>
    </div>
  );
}
