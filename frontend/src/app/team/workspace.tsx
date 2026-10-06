"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Bell, ChevronLeft, ChevronRight, Files, FolderOpen, GitBranch, HardDrive, LockKeyhole, LogOut, Menu, RefreshCw, Search, ShieldCheck, Trash2, UserRound, Users, X } from "lucide-react";
import { NotificationBell } from "../notifications";
import type { TeamAccount } from "@/lib/team";
import { teamRequest, TeamRequestError } from "@/lib/team-client";
import { getUploadQueue } from "@/lib/upload-queue";
import UploadPanel from "./files/upload-panel";
import TeamProfile, { ProfileAvatar } from "./profile";
import { useResource } from "../company/admin/workflows/workflow-ui";
import shell from "../company/admin/dashboard.module.css";
import styles from "../admin/companies/companies.module.css";
import local from "../company/admin/teams/teams.module.css";
import ManagerDashboard, { storageSize } from "./manager-dashboard";
import manager from "./manager.module.css";

type Teammate = { id: number; name: string; role: string; used_bytes: number; file_count: number; activated_at: string | null };

const mobileQuery = "(max-width: 760px)";
const subscribeMobile = (callback: () => void) => {
  const media = window.matchMedia(mobileQuery);
  media.addEventListener("change", callback);
  return () => media.removeEventListener("change", callback);
};
const getMobileSnapshot = () => window.matchMedia(mobileQuery).matches;
const getServerMobileSnapshot = () => false;

const storageBytes = (value: number) => {
  const unit = value >= 1e12 ? "TB" : value >= 1e9 ? "GB" : value >= 1e6 ? "MB" : value >= 1e3 ? "KB" : "B";
  const divisor = { TB: 1e12, GB: 1e9, MB: 1e6, KB: 1e3, B: 1 }[unit];
  return `${(value / divisor).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${unit}`;
};

export function MemberStorage({ owner }: { owner: string }) {
  const queue = getUploadQueue(owner);
  const uploadRevision = useSyncExternalStore(queue.subscribe, queue.version, () => 0);
  const [revision, setRevision] = useState(0);
  const { data, loading, error } = useResource<{ used_bytes: number; quota_bytes: number; team_used_bytes: number }>("/api/team/storage", false, revision + uploadRevision);
  const percent = data && data.quota_bytes > 0 ? Math.min(100, Math.max(0, data.used_bytes / data.quota_bytes * 100)) : 0;
  return <section className={shell.memberStorage} aria-label="Your storage" aria-busy={loading}>
    <div className={shell.storageHeading}><strong><HardDrive size={16} />Your storage</strong><button type="button" title={error ? "Retry storage usage" : "Refresh storage usage"} aria-label={error ? "Retry storage usage" : "Refresh storage usage"} disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={15} /></button></div>
    {loading ? <p role="status">Loading storage...</p> : error ? <p role="alert">Storage unavailable.</p> : data && <>
      <p><strong>{storageBytes(data.used_bytes)}</strong> used by you</p>
      <div className={shell.storageTrack} role="progressbar" aria-label="Your usage of team allocation" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-valuetext={`${storageBytes(data.used_bytes)} of ${storageBytes(data.quota_bytes)} shared team allocation`}><span style={{ width: `${percent}%` }} /></div>
      <p>{data.quota_bytes > 0 ? `${storageBytes(Math.max(0, data.quota_bytes - data.team_used_bytes))} remaining in team` : "No team allocation"}</p>
    </>}
  </section>;
}

export default function TeamWorkspace({ account: initialAccount, children, trash = false }: { account: TeamAccount; children?: ReactNode; trash?: boolean }) {
  const [profile, setProfile] = useState({ source: initialAccount, account: initialAccount });
  const [photoVersion, setPhotoVersion] = useState(0);
  if (profile.source !== initialAccount) setProfile({ source: initialAccount, account: initialAccount });
  const account = profile.source === initialAccount ? profile.account : initialAccount;
  const router = useRouter();
  const pathname = usePathname();
  const [people, setPeople] = useState<Teammate[]>([]);
  const [total, setTotal] = useState(0);
  const [driveAccess, setDriveAccess] = useState(false);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const mobile = useSyncExternalStore(subscribeMobile, getMobileSnapshot, getServerMobileSnapshot);
  const drawer = useRef<HTMLDialogElement>(null);
  const isManager = account.role === "manager";
  const workflowView = !!children;
  const role = isManager ? "Team Manager" : "Member";
  const uploadOwner = `${account.company_id}:${account.team_id}:${account.id}`;

  useEffect(() => {
    const media = window.matchMedia(mobileQuery);
    const closeMenu = () => setMenuOpen(false);
    media.addEventListener("change", closeMenu);
    return () => media.removeEventListener("change", closeMenu);
  }, []);

  useEffect(() => {
    if (!mobile || !menuOpen || !drawer.current) return;
    const dialog = drawer.current;
    const overflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = "hidden";
    return () => { dialog.close(); document.body.style.overflow = overflow; };
  }, [mobile, menuOpen]);

  useEffect(() => {
    if (!isManager || workflowView) return;
    const controller = new AbortController();
    async function load() {
      setLoading(true); setError("");
      const params = new URLSearchParams({ search: query, page: String(page), page_size: String(pageSize) });
      if (from) params.set("from_date", from);
      if (to) params.set("to_date", to);
      try {
        const result = await teamRequest<{ items: Teammate[]; total: number; manager_can_view_drives: boolean }>(`/api/team/people?${params}`, undefined, controller.signal);
        if (!controller.signal.aborted) { setPeople(result.items); setTotal(result.total); setDriveAccess(result.manager_can_view_drives); }
      } catch (failure) {
        if (!controller.signal.aborted) {
          if (failure instanceof TeamRequestError && failure.status === 401) { router.replace("/"); router.refresh(); }
          setError(failure instanceof Error ? failure.message : "Unable to load your team.");
        }
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [isManager, workflowView, account.team_id, query, from, to, page, pageSize, revision, router]);

  async function logout() {
    if (signingOut) return;
    if (getUploadQueue(uploadOwner).snapshot().some(item => item.status === "queued" || item.status === "uploading")) {
      setLogoutError("Wait for your uploads to finish before signing out."); return;
    }
    setSigningOut(true); setLogoutError("");
    try { await teamRequest("/api/team/auth/logout", {}); getUploadQueue(uploadOwner).dismiss(); router.replace("/"); router.refresh(); }
    catch (failure) { setLogoutError(failure instanceof Error ? failure.message : "Unable to sign out."); }
    finally { setSigningOut(false); }
  }

  const sidebar = <aside className={shell.sidebar} aria-label="Team workspace" onClick={event => { if ((event.target as HTMLElement).closest("a")) setMenuOpen(false); }}>
      {mobile && <button type="button" className={shell.drawerClose} aria-label="Close navigation" title="Close navigation" onClick={() => setMenuOpen(false)} autoFocus><X size={22} /></button>}
      <Link href="/team/files" className={shell.brand}><Image src="/mikan-logo.jpg" alt="Mikan Engineering" width={104} height={42} priority /></Link>
      <nav className={shell.navigation}><p>WORKSPACE</p>{isManager && <Link href={workflowView ? "/team#team-content" : "#team-content"} className={shell.navItem} aria-current={!workflowView || pathname === "/team/drives" ? "page" : undefined}><Users size={18} /><span>My Team</span></Link>}<Link href="/team/files" className={shell.navItem} aria-current={pathname.startsWith("/team/files") && !trash ? "page" : undefined}><Files size={18} /><span>My Files</span></Link><Link href="/team/files?scope=trash" className={shell.navItem} aria-current={trash ? "page" : undefined}><Trash2 size={18} /><span>Trash</span></Link><Link href="/team/workflows" className={shell.navItem} aria-current={pathname.startsWith("/team/workflows") ? "page" : undefined}><GitBranch size={18} /><span>Workflows</span></Link><Link href={workflowView ? "/team#my-account" : "#my-account"} className={shell.navItem} aria-current={!isManager && !workflowView ? "page" : undefined}><UserRound size={18} /><span>My Profile</span></Link></nav>
      <Link href="/team/notifications" className={shell.navItem} aria-current={pathname === "/team/notifications" ? "page" : undefined}><Bell size={18} /><span>Notifications</span></Link>
      <div className={shell.sidebarBottom}><MemberStorage key={`${uploadOwner}:${pathname}`} owner={uploadOwner} /><div className={shell.identity}><ProfileAvatar name={account.name} source={account.has_photo ? `/api/team/profile/photo?v=${photoVersion}` : undefined} small /><div><strong>{account.name}</strong><span>{role}</span></div></div><button className={styles.secondary} onClick={() => void logout()} disabled={signingOut}><LogOut size={16} />{signingOut ? "Signing out..." : "Sign out"}</button></div>
    </aside>;

  return <div className={`${shell.shell} ${shell.teamShell}`}>
    <a href="#team-content" className={shell.skipLink}>Skip to content</a>
    {mobile ? <dialog ref={drawer} id="team-navigation" className={shell.teamDrawer} aria-label="Team navigation" onCancel={() => setMenuOpen(false)} onClose={() => setMenuOpen(false)} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); setMenuOpen(false); } }} onClick={event => {
      if (event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) setMenuOpen(false);
    }}>{sidebar}</dialog> : sidebar}
    <main className={shell.page} id="team-content" tabIndex={-1}>
      <header className={shell.header}><button type="button" className={shell.menuToggle} aria-label="Open navigation" title="Open navigation" aria-expanded={mobile && menuOpen} aria-controls={mobile ? "team-navigation" : undefined} onClick={() => setMenuOpen(true)}><Menu size={22} /></button><div className={shell.welcome}><strong>Welcome back, {account.name}</strong><span>{account.company_name}</span></div><div className={shell.headerActions}><NotificationBell scope="team" /><span className={shell.headerAccount}><ShieldCheck size={18} /><span>{role}</span></span></div></header>
      {children}
      {workflowView && logoutError && <p className={styles.formError} role="alert">{logoutError}</p>}
      {isManager && !workflowView && <section className={styles.section}>
        <div className={`${styles.heading} ${local.heading}`}><div><p className={styles.eyebrow}>{account.team_name}</p><h1>My Team</h1></div></div>
        {logoutError && <p className={styles.formError} role="alert">{logoutError}</p>}
        <ManagerDashboard owner={uploadOwner} revision={revision} />
        <div className={manager.heading}><h2 id="team-members" className={manager.memberHeading}>Team members</h2><button type="button" title="Refresh team members" aria-label="Refresh team members" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={17} /></button></div>
        <div className={styles.filters}>
          <form className={styles.search} onSubmit={event => { event.preventDefault(); setQuery(search.trim()); setPage(1); }}><Search size={17} /><input aria-label="Search team members" placeholder="Search team members" value={search} maxLength={160} onChange={event => setSearch(event.target.value)} /><button className={styles.iconButton} title="Search" aria-label="Search"><ChevronRight size={18} /></button></form>
          <label className={styles.date}>Joined from<input type="date" value={from} max={to || undefined} onChange={event => { setFrom(event.target.value); setPage(1); }} /></label>
          <label className={styles.date}>Joined to<input type="date" value={to} min={from || undefined} onChange={event => { setTo(event.target.value); setPage(1); }} /></label>
          {(query || from || to) && <button className={styles.secondary} onClick={() => { setSearch(""); setQuery(""); setFrom(""); setTo(""); setPage(1); }}>Clear</button>}
        </div>
        <div aria-busy={loading} aria-live="polite">
          {loading ? <div className={styles.skeleton} role="status" aria-label="Loading your team">{[0, 1, 2].map(row => <div key={row}><span /><span /><span /></div>)}</div> : error ? <div className={styles.empty} role="alert"><p>{error}</p><button className={styles.secondary} onClick={() => setRevision(value => value + 1)}>Retry</button></div> : !people.length ? <div className={styles.empty}>No matching team members</div> : <>
            <div className={`${styles.tableWrap} ${manager.memberTableWrap}`} tabIndex={0} role="region" aria-labelledby="team-members">
              <table className={`${styles.table} ${manager.memberTable}`}>
                <thead><tr><th scope="col">Member</th><th scope="col">Role</th><th scope="col">Storage used</th><th scope="col">Files</th><th scope="col">Joined</th><th scope="col" aria-label="Drive"><FolderOpen size={15} aria-hidden="true" /></th></tr></thead>
                <tbody>{people.map(person => <tr key={person.id}>
                  <td><div className={manager.memberIdentity}><span className={manager.memberInitial} aria-hidden="true">{person.name.trim().charAt(0).toUpperCase()}</span><strong title={person.name}>{person.name}</strong>{person.id === account.id && <span className={manager.selfLabel}>(You)</span>}</div></td>
                  <td>{person.role === "manager" ? "Team Manager" : "Member"}</td>
                  <td>{storageSize(person.used_bytes)}</td><td>{person.file_count}</td>
                  <td>{person.activated_at ? new Date(person.activated_at).toLocaleDateString("en-GB", { timeZone: "Asia/Kolkata" }) : "Not recorded"}</td>
                  <td>{driveAccess ? <Link className={manager.memberDriveAction} href={`/team/drives?owner=${person.id}`} title={`Open ${person.name}'s drive`} aria-label={`Open ${person.name}'s drive`}><FolderOpen size={16} /></Link> : <span className={manager.memberDriveAction} title="Drive access restricted" role="img" aria-label="Drive access restricted"><LockKeyhole size={15} aria-hidden="true" /></span>}</td>
                </tr>)}</tbody>
              </table>
            </div>
            <div className={styles.mobileList}>{people.map(person => <article className={`${styles.mobileItem} ${local.person} ${manager.memberCard}`} key={person.id}><strong>{person.name}{person.id === account.id ? " (Myself)" : ""}</strong><span className={local.sub}>{person.role === "manager" ? "Team Manager" : "Member"}</span><div className={manager.memberMeta}><span>{storageSize(person.used_bytes)} used</span><span>{person.file_count} files</span><span>Joined {person.activated_at ? new Date(person.activated_at).toLocaleDateString("en-GB", { timeZone: "Asia/Kolkata" }) : "Not recorded"}</span></div>{driveAccess ? <Link className={manager.driveLink} href={`/team/drives?owner=${person.id}`}><Files size={16} />Open drive</Link> : <span className={manager.memberMeta}>Drive access restricted</span>}</article>)}</div>
          </>}
        </div>
        {!loading && !error && total > 0 && <div className={`${styles.pagination} ${manager.memberPagination}`}><span>{(page - 1) * pageSize + 1}-{Math.min(page * pageSize, total)} of {total} members</span><div><label>Rows per page<select value={pageSize} onChange={event => { setPageSize(Number(event.target.value)); setPage(1); }}><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option></select></label><button className={styles.iconButton} disabled={page === 1} onClick={() => setPage(value => value - 1)} title="Previous page" aria-label="Previous page"><ChevronLeft size={18} /></button><span>Page {page} of {Math.max(1, Math.ceil(total / pageSize))}</span><button className={styles.iconButton} disabled={page * pageSize >= total} onClick={() => setPage(value => value + 1)} title="Next page" aria-label="Next page"><ChevronRight size={18} /></button></div></div>}
      </section>}
      {!workflowView && <>{!isManager && logoutError && <p className={styles.formError} role="alert">{logoutError}</p>}<TeamProfile key={account.id} account={account} photoVersion={photoVersion} onSaved={updated => { setProfile({ source: initialAccount, account: updated }); setPhotoVersion(value => value + 1); setRevision(value => value + 1); router.refresh(); }} /></>}
      <footer className={shell.footer}><span>Mikan Engineering</span><a href="https://zyoralabs.com" target="_blank" rel="noopener noreferrer">Powered by Zyora Labs</a></footer>
    </main>
    <UploadPanel key={uploadOwner} owner={uploadOwner} />
  </div>;
}