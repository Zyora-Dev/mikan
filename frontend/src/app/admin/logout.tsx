"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import styles from "./admin.module.css";

export default function Logout({ company = false }: { company?: boolean }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function logout() {
    setPending(true);
    setError("");
    try {
      const response = await fetch(company ? "/api/company/admin/logout" : "/api/admin/logout", { method: "POST" });
      if (!response.ok) throw new Error("Sign out failed.");
      router.replace(company ? "/company/admin/login" : "/admin/login");
      router.refresh();
    } catch {
      setError("Unable to sign out. Please try again.");
      setPending(false);
    }
  }
  return <div className={styles.logout}><button onClick={logout} disabled={pending}><LogOut size={16} />{pending ? "Signing out" : "Sign out"}</button>{error && <p role="alert">{error}</p>}</div>;
}