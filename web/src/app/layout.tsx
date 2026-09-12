import type { Metadata } from "next";
import localFont from "next/font/local";
import "../styles/globals.css";

// Self-hosted fonts (see web/public/fonts/LICENSES.md for licence details).
// Each `variable` targets the same custom property tokens.css declares in its
// `--font-*` fallback stack, so this is the only place a font name is spelled
// outside tokens.css: next/font overrides the CSS variable on <html> with the
// self-hosted @font-face family, ahead of the literal fallback names in tokens.css.
const clashDisplay = localFont({
  src: [
    { path: "../../public/fonts/ClashDisplay-Regular.woff2", weight: "400", style: "normal" },
    { path: "../../public/fonts/ClashDisplay-Medium.woff2", weight: "500", style: "normal" },
    { path: "../../public/fonts/ClashDisplay-Semibold.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-display",
  display: "swap",
  fallback: ["Geist", "system-ui", "sans-serif"],
});

const geist = localFont({
  src: [
    { path: "../../public/fonts/Geist-Regular.woff2", weight: "400", style: "normal" },
    { path: "../../public/fonts/Geist-Medium.woff2", weight: "500", style: "normal" },
    { path: "../../public/fonts/Geist-SemiBold.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-body",
  display: "swap",
  fallback: ["system-ui", "sans-serif"],
});

const jetbrainsMono = localFont({
  src: [
    { path: "../../public/fonts/JetBrainsMono-Regular.woff2", weight: "400", style: "normal" },
    { path: "../../public/fonts/JetBrainsMono-Medium.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-mono",
  display: "swap",
  fallback: ["ui-monospace", "monospace"],
});

export const metadata: Metadata = {
  title: "Sieve",
  description: "Sieve — a triage layer for MCP tool-poisoning scanner findings.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${clashDisplay.variable} ${geist.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
