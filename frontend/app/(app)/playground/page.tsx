import { AnimatedCard } from "@/components/AnimatedCard";
import { PlaySquare, Send, Server, User, Cpu } from "lucide-react";

export default function Playground() {
  return (
    <div className="min-h-[calc(100vh-64px)] bg-surface flex flex-col text-on-surface">
      <main className="flex-1 max-w-[1600px] w-full mx-auto p-4 md:p-8 lg:p-16 flex gap-6 xl:gap-12">
        
        {/* Sidebar settings */}
        <aside className="w-64 flex-shrink-0 flex flex-col gap-6">
          <div>
            <h2 className="text-sm font-semibold tracking-wider text-on-surface uppercase mb-4">Configuration</h2>
            <div className="space-y-4">
              <div className="flex flex-col gap-2">
                <label className="text-xs text-on-surface-variant uppercase tracking-wide">Provider</label>
                <select className="bg-surface-container-low border border-white/[0.05] rounded-md px-3 py-2 text-sm text-on-surface focus:outline-none focus:border-primary">
                  <option>Groq</option>
                  <option>OpenRouter</option>
                  <option>Anthropic</option>
                  <option>Cohere</option>
                </select>
              </div>

              <div className="flex flex-col gap-2">
                <label className="text-xs text-on-surface-variant uppercase tracking-wide">Model</label>
                <select className="bg-surface-container-low border border-white/[0.05] rounded-md px-3 py-2 text-sm text-on-surface focus:outline-none focus:border-primary">
                  <option>Llama 3 8B (Groq)</option>
                  <option>Mixtral 8x7B (Groq)</option>
                  <option>Claude 3.5 Sonnet</option>
                </select>
              </div>
            </div>
          </div>
          
          <div>
            <h2 className="text-sm font-semibold tracking-wider text-on-surface uppercase mb-4">Enabled Tools</h2>
            <div className="space-y-2">
              {['search_web', 'read_file', 'run_command', 'get_weather'].map((tool) => (
                <label key={tool} className="flex items-center gap-3 text-sm text-on-surface-variant cursor-pointer">
                  <input type="checkbox" defaultChecked className="accent-primary w-4 h-4 rounded-sm border-white/10 bg-surface-container" />
                  {tool}
                </label>
              ))}
            </div>
          </div>
        </aside>

        {/* Chat / Playground Area */}
        <section className="flex-1 flex flex-col min-h-0 bg-surface-container-lowest rounded-xl border border-white/[0.05] overflow-hidden">
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            
            <div className="flex gap-4">
              <div className="size-8 rounded-full bg-surface-container-high flex items-center justify-center border border-white/10 flex-shrink-0">
                <User className="size-4 text-primary" />
              </div>
              <div className="flex-1 space-y-2">
                <p className="text-sm text-on-surface leading-relaxed">
                  Can you check the current weather in Tokyo and then run a quick search for top events happening there this weekend?
                </p>
              </div>
            </div>

            <div className="flex gap-4">
              <div className="size-8 rounded-full bg-primary/10 flex items-center justify-center border border-primary/20 flex-shrink-0">
                <Cpu className="size-4 text-primary" />
              </div>
              <div className="flex-1 space-y-4">
                <p className="text-sm text-on-surface leading-relaxed">
                  I will use my tools to gather that information for you.
                </p>
                
                <AnimatedCard className="p-3 border-l-2 border-l-tertiary bg-surface-container border-y-white/[0.05] border-r-white/[0.05] flex flex-col gap-2">
                  <div className="flex items-center gap-2 text-xs font-mono text-tertiary">
                    <Server className="size-3" />
                    <span>Tool Call: get_weather</span>
                  </div>
                  <pre className="text-xs text-on-surface-variant font-mono">
                    {`{"location": "Tokyo", "unit": "celsius"}`}
                  </pre>
                </AnimatedCard>
                
                <AnimatedCard className="p-3 border-l-2 border-l-tertiary/50 bg-surface-container-low border-y-white/[0.02] border-r-white/[0.02] flex flex-col gap-2">
                   <div className="flex items-center gap-2 text-xs font-mono text-on-surface-variant">
                    <Server className="size-3 opacity-50" />
                    <span>Result</span>
                  </div>
                  <p className="text-xs text-on-surface-variant font-mono">
                    22°C, mostly cloudy.
                  </p>
                </AnimatedCard>

                <p className="text-sm text-on-surface leading-relaxed">
                  Currently, it is 22°C and mostly cloudy in Tokyo. Now, I will search for the events...
                </p>
              </div>
            </div>

          </div>

          <div className="p-4 bg-surface border-t border-white/[0.05]">
            <div className="relative flex items-center">
              <textarea 
                className="w-full bg-surface-container border border-white/[0.08] rounded-lg pl-4 pr-12 py-3 text-sm text-on-surface focus:outline-none focus:border-primary resize-none placeholder:text-on-surface-variant/50"
                placeholder="Message MTP Agent..."
                rows={1}
              />
              <button className="absolute right-2 p-1.5 bg-primary text-on-primary rounded-md hover:bg-primary-fixed transition-colors">
                <Send className="size-4" />
              </button>
            </div>
            <p className="text-center text-xs text-on-surface-variant/60 mt-3">
              MTP Agents can make mistakes. Consider verifying important information.
            </p>
          </div>
        </section>

      </main>
    </div>
  );
}
