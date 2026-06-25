import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { siteUrl } from "@/content/site";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: "MTPX Documentation",
  description: "Motion-led documentation for MTPX, a multi-tool protocol runtime and SDK for inspectable agent systems.",
  alternates: {
    canonical: siteUrl
  },
  openGraph: {
    title: "MTPX Documentation",
    description: "Documentation for MTPX, a multi-tool protocol runtime and SDK for inspectable agent systems.",
    url: siteUrl,
    siteName: "MTPX Documentation",
    type: "website"
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <Header />
          {children}
          <Footer />
        </Providers>
      </body>
    </html>
  );
}
