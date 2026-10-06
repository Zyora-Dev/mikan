"use client";

import Image from "next/image";
import { useRef, useState, type FormEvent } from "react";
import { Building2, Camera, Check, LockKeyhole, Mail, Pencil, Phone, Save, ShieldCheck, Trash2, UserRound, Users, X } from "lucide-react";
import type { TeamAccount } from "@/lib/team";
import { teamRequest } from "@/lib/team-client";
import styles from "./profile.module.css";

export function ProfileAvatar({ name, source, small = false }: { name: string; source?: string; small?: boolean }) {
  const [failedSource, setFailedSource] = useState("");
  return <span className={small ? styles.smallAvatar : styles.avatar}>
    {source && failedSource !== source ? <Image src={source} alt={`${name}'s profile photo`} width={112} height={112} unoptimized onError={() => setFailedSource(source)} /> : <span aria-label={name}>{name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase()}</span>}
  </span>;
}

export default function TeamProfile({ account, photoVersion, onSaved }: { account: TeamAccount; photoVersion: number; onSaved: (account: TeamAccount) => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(account.name);
  const [mobile, setMobile] = useState(account.mobile);
  const [photo, setPhoto] = useState<string | null>(null);
  const [removePhoto, setRemovePhoto] = useState(false);
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const operation = useRef(false);
  const busy = reading || saving;
  const dirty = name.trim() !== account.name || mobile.trim() !== account.mobile || photo !== null || removePhoto;
  const role = account.role === "manager" ? "Team Manager" : "Member";
  const source = editing && removePhoto ? undefined : editing && photo ? photo : account.has_photo ? `/api/team/profile/photo?v=${photoVersion}` : undefined;

  function edit() {
    setName(account.name); setMobile(account.mobile); setPhoto(null); setRemovePhoto(false);
    setError(""); setMessage(""); setEditing(true);
  }

  function cancel() {
    if (operation.current) return;
    setPhoto(null); setRemovePhoto(false); setError(""); setEditing(false);
    editButton.current?.focus();
  }

  async function choosePhoto(file?: File) {
    if (!file || operation.current) return;
    setError("");
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size === 0 || file.size > 2 * 1024 ** 2) {
      setError("Choose a JPG, PNG or WebP photo up to 2 MB."); return;
    }
    operation.current = true; setReading(true);
    try {
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = reader.onabort = () => reject(new Error("Photo could not be read. Choose it again."));
        reader.readAsDataURL(file);
      });
      setPhoto(encoded); setRemovePhoto(false);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Photo could not be read."); }
    finally { operation.current = false; setReading(false); }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (operation.current || !dirty) return;
    if (!name.trim() || !/^\+?[0-9 ()-]+$/.test(mobile.trim()) || !/^[0-9]{7,15}$/.test(mobile.replace(/\D/g, ""))) {
      setError("Enter your name and a mobile number with 7-15 digits."); return;
    }
    operation.current = true; setSaving(true); setError(""); setMessage("");
    try {
      const updated = await teamRequest<TeamAccount>("/api/team/profile", {
        name: name.trim(), mobile: mobile.trim(), photo_base64: photo?.split(",")[1] ?? null, remove_photo: removePhoto,
      });
      onSaved(updated); setEditing(false); setPhoto(null); setRemovePhoto(false); setMessage("Profile updated.");
      editButton.current?.focus();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Profile could not be saved. Try again."); }
    finally { operation.current = false; setSaving(false); }
  }

  return <section className={styles.profile} id="my-account" aria-labelledby="profile-title">
    <div className={styles.titleRow}><div><span className={styles.eyebrow}>MY ACCOUNT</span>{account.role === "manager" ? <h2 id="profile-title">My Profile</h2> : <h1 id="profile-title">My Profile</h1>}</div>
      <button ref={editButton} type="button" className={styles.secondary} hidden={editing} onClick={edit}><Pencil size={16} />Edit profile</button>
    </div>
    <form onSubmit={event => void save(event)} aria-busy={busy}>
      <div className={styles.identity}>
        <div className={styles.photoWrap}><ProfileAvatar name={account.name} source={source} />
          <button type="button" className={styles.camera} title={source ? "Change profile photo" : "Upload profile photo"} aria-label={source ? "Change profile photo" : "Upload profile photo"} disabled={busy} onClick={() => { if (!editing) edit(); input.current?.click(); }}><Camera size={18} /></button>
        </div>
        <div className={styles.identityText}><h3>{account.name}</h3><p>{account.email}</p><div className={styles.badges}><span><ShieldCheck size={14} />{role}</span><span><Users size={14} />{account.team_name}</span></div></div>
        <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" aria-label="Profile photo file" hidden disabled={busy} onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; void choosePhoto(file); }} />
      </div>
      {editing && <div className={styles.photoActions}><button type="button" className={styles.secondary} disabled={busy} onClick={() => input.current?.click()}><Camera size={16} />{reading ? "Reading photo..." : source ? "Change photo" : "Upload photo"}</button>
        {source && <button type="button" className={styles.remove} disabled={busy} onClick={() => { setPhoto(null); setRemovePhoto(account.has_photo); setError(""); }}><Trash2 size={16} />Remove photo</button>}
        <span>JPG, PNG or WebP. Max 2 MB.</span>
      </div>}
      {error && <p className={styles.error} role="alert">{error}</p>}
      {message && <p className={styles.success} role="status"><Check size={17} />{message}</p>}
      <div className={styles.sections}>
        <section className={styles.details} aria-labelledby="personal-title"><h3 id="personal-title"><UserRound size={18} />Personal details</h3>
          {editing ? <div className={styles.fields}><label htmlFor="profile-name">Full name<input id="profile-name" name="name" autoComplete="name" value={name} maxLength={120} required disabled={busy} onChange={event => setName(event.target.value)} /></label>
            <label htmlFor="profile-mobile">Mobile number<input id="profile-mobile" name="tel" type="tel" autoComplete="tel" value={mobile} minLength={7} maxLength={25} required disabled={busy} onChange={event => setMobile(event.target.value)} /></label></div>
            : <dl><div><dt><UserRound size={16} />Full name</dt><dd>{account.name}</dd></div><div><dt><Phone size={16} />Mobile number</dt><dd>{account.mobile}</dd></div></dl>}
          <dl><div><dt><Mail size={16} />Email address</dt><dd>{account.email}</dd></div></dl>
        </section>
        <section className={styles.access} aria-labelledby="access-title"><div className={styles.sectionTitle}><h3 id="access-title"><LockKeyhole size={18} />Workspace access</h3><span className={styles.managed}>Admin managed</span></div>
          <dl><div><dt><Building2 size={16} />Company</dt><dd>{account.company_name}</dd></div><div><dt><Users size={16} />Team</dt><dd>{account.team_name}</dd></div><div><dt><ShieldCheck size={16} />Role</dt><dd>{role}</dd></div><div><dt><LockKeyhole size={16} />Sign-in method</dt><dd>{account.auth_type === "otp" ? "Email OTP" : "Password"}</dd></div></dl>
        </section>
      </div>
      {editing && <div className={styles.saveBar}><span>{dirty ? "Unsaved changes" : "No changes"}</span><div><button type="button" className={styles.secondary} disabled={busy} onClick={cancel}><X size={16} />Cancel</button><button type="submit" className={styles.primary} disabled={busy || !dirty}><Save size={16} />{saving ? "Saving..." : "Save changes"}</button></div></div>}
    </form>
  </section>;
}