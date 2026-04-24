import type { Metadata } from "next";
import { Space_Grotesk, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["300", "400", "500", "700"],
});

export const metadata: Metadata = {
  title: "MTPX — Model Tool Protocol Extended",
  description:
    "Protocol-first Python SDK for AI agent tool orchestration. Structured DAG execution plans, 13+ provider adapters, safety policies, and persistent sessions — production-ready from day one.",
  keywords: ["AI agents", "tool orchestration", "MTP", "MTPX", "Python", "LLM", "agent SDK", "model tool protocol"],
  icons: {
    icon: "/mtp-logo.png",
    shortcut: "/mtp-logo.png",
    apple: "/mtp-logo.png",
  },
  openGraph: {
    title: "MTPX — Model Tool Protocol Extended",
    description: "Build production-grade AI agents with structured execution plans and deterministic tool orchestration.",
    type: "website",
    images: [{ url: "/mtp-logo.png", width: 1080, height: 1080, alt: "MTPX Logo" }],
  },
  twitter: {
    card: "summary",
    title: "MTPX — Model Tool Protocol Extended",
    description: "Protocol-first Python SDK for AI agent tool orchestration.",
    images: ["/mtp-logo.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${spaceGrotesk.variable} ${jetbrainsMono.variable} h-full antialiased dark`}
    >
      <body className="h-full flex flex-col bg-[#030305] text-[#f0f0f5] overflow-x-hidden">
        {children}
      </body>
    </html>
  );
}
