// LoginGate — the direct-mode Cognito sign-in gate (FEAT-003).
//
// In VITE_DATA_MODE=direct the deployed Amplify SPA must authenticate against
// the Cognito User Pool before any tab makes an AWS call: the DirectAwsDataClient
// resolves Identity Pool SigV4 creds from the active session (auth.ts), and with
// no session every data call throws "Not signed in". This component wires the
// signIn / NewPasswordRequired / setCurrentSession machinery from auth.ts into an
// actual sign-in screen and blocks `children` until a session exists.
//
// In the DEFAULT 'bff' mode this renders `children` straight through — the local
// path is unchanged and never shows a login (no Cognito dependency is touched).

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { getDataMode } from "./mode";
import {
  signIn,
  restoreSession,
  setCurrentSession,
  NewPasswordRequired,
  type AuthSession,
} from "./auth";
import Button from "../components/Button";
import styles from "./LoginGate.module.css";

/** Normalize an unknown throw into a user-facing message. */
function messageOf(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "Sign-in failed. Check your credentials and try again.";
}

export default function LoginGate({ children }: { children: ReactNode }) {
  // The bff path never gates — short-circuit before any Cognito work.
  if (getDataMode() !== "direct") return <>{children}</>;
  return <DirectLoginGate>{children}</DirectLoginGate>;
}

function DirectLoginGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  // While a reload tries to reuse a cached Cognito refresh token.
  const [restoring, setRestoring] = useState(true);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // Set when Cognito demands a first-login password change; holds the resolver.
  const [challenge, setChallenge] = useState<NewPasswordRequired | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // On mount, try to restore a cached session so a reload skips the form.
  useEffect(() => {
    let active = true;
    restoreSession()
      .then((restored) => {
        if (!active) return;
        if (restored) {
          setCurrentSession(restored);
          setSession(restored);
        }
      })
      .catch(() => {
        /* no cached session; fall through to the form */
      })
      .finally(() => {
        if (active) setRestoring(false);
      });
    return () => {
      active = false;
    };
  }, []);

  function activate(next: AuthSession) {
    setCurrentSession(next);
    setSession(next);
    setPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setChallenge(null);
  }

  async function onSignIn(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const next = await signIn(username.trim(), password);
      activate(next);
    } catch (err) {
      if (err instanceof NewPasswordRequired) {
        // Seeded operator's first login: switch to the new-password step.
        setChallenge(err);
      } else {
        setError(messageOf(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function onCompleteNewPassword(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPassword !== confirmPassword) {
      setError("The two passwords do not match.");
      return;
    }
    if (!challenge) return;
    setBusy(true);
    try {
      const next = await challenge.complete(newPassword);
      activate(next);
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  // A valid session exists — hand control to the operator tabs.
  if (session) return <>{children}</>;

  // Avoid flashing the form while a cached session is being restored.
  if (restoring) {
    return (
      <div className={styles.screen}>
        <div className={styles.card}>
          <p className={styles.hint}>Restoring session…</p>
        </div>
      </div>
    );
  }

  const firstLogin = challenge !== null;

  return (
    <div className={styles.screen}>
      <div className={styles.card}>
        <div className={styles.brand}>
          <span className={styles.brandMark} />
          <span className={styles.brandTitle}>Advisor Copilot</span>
        </div>
        <div className={styles.heading}>
          <span className={styles.title}>
            {firstLogin ? "Set a new password" : "Sign in"}
          </span>
          <span className={styles.subtitle}>
            {firstLogin
              ? "Choose a new password to finish your first sign-in."
              : "Sign in with your Cognito operator credentials."}
          </span>
        </div>

        {error && <div className={styles.error}>{error}</div>}

        {firstLogin ? (
          <form className={styles.form} onSubmit={onCompleteNewPassword}>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="newPassword">
                New password
              </label>
              <input
                id="newPassword"
                className={styles.input}
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
              />
            </div>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="confirmPassword">
                Confirm new password
              </label>
              <input
                id="confirmPassword"
                className={styles.input}
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
              />
            </div>
            <Button type="submit" disabled={busy}>
              {busy ? "Setting password…" : "Set password & continue"}
            </Button>
          </form>
        ) : (
          <form className={styles.form} onSubmit={onSignIn}>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="username">
                Username
              </label>
              <input
                id="username"
                className={styles.input}
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="password">
                Password
              </label>
              <input
                id="password"
                className={styles.input}
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <Button type="submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
