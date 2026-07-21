import type { Metadata } from "next";
import { Space_Mono, IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

const displayFont = Space_Mono({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["400", "700"],
});

const bodyFont = IBM_Plex_Sans({
  variable: "--font-body",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const dataFont = IBM_Plex_Mono({
  variable: "--font-data",
  subsets: ["latin"],
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "Kubernaut",
  description: "Chat with the Kubernaut SRE agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${displayFont.variable} ${bodyFont.variable} ${dataFont.variable} h-full`}
    >
      <body className="h-screen overflow-hidden bg-background text-foreground antialiased">
        <div className="flex h-full">
          <Sidebar />
          <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
        </div>
      </body>
    </html>
  );
}
