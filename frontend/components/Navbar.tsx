"use client";

import Link from "next/link";
import Image from "next/image";
import { ChevronDown, Globe, Hexagon, ChevronRight } from "lucide-react";

export function Navbar() {
  return (
    <nav className="sticky top-0 z-50 w-full h-16 border-b border-white/[0.08] bg-black flex items-center justify-between px-4 md:px-6">
      
      {/* Left Base with Logo */}
      <div className="flex items-center gap-6">
        <Link href="/" className="flex items-center gap-2.5 group">
          {/* Logo image */}
          <div className="relative size-10 flex-shrink-0">
            <div className="absolute inset-0 rounded-lg bg-[#facc15] opacity-0 group-hover:opacity-20 blur-md transition-opacity duration-500" />
            <Image
              src="/mtp-logo.png"
              alt="MTPX Logo"
              width={40}
              height={40}
              className="relative z-10 rounded-lg"
              priority
            />
          </div>
          {/* Wordmark */}
          <div className="flex flex-col leading-none">
            <span className="font-bold text-[18px] tracking-tight text-white">
              mtpx<span className="text-[#facc15]">.</span>
            </span>
          </div>
        </Link>
        
        <div className="flex items-center gap-1 text-white/50 hover:text-white ml-2 text-sm cursor-pointer transition-colors">
          <span>Platform</span>
          <ChevronDown className="size-3" />
        </div>
      </div>

      {/* Middle Pills Nav */}
      <div className="hidden md:flex flex-1 items-center justify-center gap-1">
        {[
          { name: "Build", active: true },
          { name: "Admin" },
          { name: "Models & pricing" },
          { name: "Client SDKs" },
          { name: "API Reference" },
        ].map((item) => (
          <button
            key={item.name}
            className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
              item.active 
                ? "bg-white/10 text-white" 
                : "text-white/50 hover:text-white hover:bg-white/5"
            }`}
          >
            {item.name}
          </button>
        ))}
      </div>

      {/* Right Controls */}
      <div className="flex items-center gap-4">
        <button className="hidden sm:flex items-center gap-1 text-sm text-white/50 hover:text-white transition-colors">
          <Globe className="size-4" />
          <span>English</span>
          <ChevronDown className="size-3" />
        </button>
        <button className="bg-white text-black hover:bg-white/90 text-sm font-medium px-4 py-1.5 rounded-md transition-colors">
          Log In
        </button>
      </div>

    </nav>
  );
}
