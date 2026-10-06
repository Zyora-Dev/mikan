import type { Metadata } from "next";
import "@fontsource/geist/400.css";
import "@fontsource/geist/500.css";
import "@fontsource/geist/600.css";
import "@fontsource/geist/700.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sign In | Mikan Cloud Workspace",
  description: "Mikan Engineering's private cloud workspace for your files and teams.",
  robots: { index: false, follow: false },
  icons: {
    icon: { url: "https://media.zyora.in/images/logo/zyora-fav-white.png", type: "image/png" },
    shortcut: "https://media.zyora.in/images/logo/zyora-fav-white.png",
    apple: "https://media.zyora.in/images/logo/zyora-fav-white.png",
  },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
