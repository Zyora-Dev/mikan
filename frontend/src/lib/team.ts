import "server-only";
import { cookies } from "next/headers";
import { adminBackend } from "./admin";

export const teamCookie = "mikan_team_session";
export type TeamAccount = { id: number; name: string; email: string; mobile: string; role: "manager" | "member"; auth_type: "otp" | "password"; team_id: number; team_name: string; company_id: number; company_name: string; has_photo: boolean };

export async function getTeamAccount(): Promise<TeamAccount | null> {
  const token = (await cookies()).get(teamCookie)?.value;
  if (!token) return null;
  const response = await fetch(`${adminBackend}/auth/team/me`, { headers: { Cookie: `${teamCookie}=${encodeURIComponent(token)}` }, cache: "no-store", signal: AbortSignal.timeout(8000) });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error("Team service unavailable.");
  return response.json();
}