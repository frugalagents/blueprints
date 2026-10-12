// Direct-mode (FEAT-002) Cognito authentication.
//
// Two-step browser auth per the approved plan (cloud-deploy-impl-plan.md §B):
//
//   1. LOGIN GATE — sign in to the Cognito USER POOL with
//      amazon-cognito-identity-js. This proves identity and yields the user's
//      id/access/refresh tokens. The seeded operator starts in
//      FORCE_CHANGE_PASSWORD, so the first login must complete a
//      new-password challenge.
//
//   2. SIGV4 CREDENTIALS — the AgentCore data-plane InvokeAgentRuntime (and all
//      the CloudWatch/registry/policy reads) are SigV4-signed, so the browser
//      exchanges the user-pool ID TOKEN for temporary AWS credentials from the
//      Cognito IDENTITY POOL via fromCognitoIdentityPool. The user-pool JWT is
//      NEVER used to call AWS directly — only the Identity Pool SigV4 creds are.
//
// The resulting AwsCredentialIdentityProvider is handed to every AWS SDK v3
// client the DirectAwsDataClient builds.

import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
  type CognitoUserSession,
} from "amazon-cognito-identity-js";
// Import fromCognitoIdentityPool from the focused provider package rather than
// the @aws-sdk/credential-providers barrel: the barrel re-exports Node-only
// providers (SSO/process/login) that import node:fs/os/crypto and break the
// browser bundle. This package is browser-safe and the only provider we need.
import { fromCognitoIdentityPool } from "@aws-sdk/credential-provider-cognito-identity";
import type { AwsCredentialIdentityProvider } from "@aws-sdk/types";
import { awsConfig, requireConfig } from "./aws-config";

/** The signed-in session: the id token + a SigV4 credential provider. */
export interface AuthSession {
  /** The Cognito user-pool ID token (JWT) — the Identity Pool login key. */
  idToken: string;
  /** The user's login name (sub/username) for display. */
  username: string;
  /** SigV4 temporary credentials from the Identity Pool for all SDK clients. */
  credentials: AwsCredentialIdentityProvider;
}

/** Thrown when a first-login new-password challenge is required. */
export class NewPasswordRequired extends Error {
  constructor(
    /** Re-invoke completeNewPassword() with this to finish the first login. */
    public readonly complete: (newPassword: string) => Promise<AuthSession>
  ) {
    super("A new password is required to complete the first sign-in.");
    this.name = "NewPasswordRequired";
  }
}

function userPool(): CognitoUserPool {
  return new CognitoUserPool({
    UserPoolId: requireConfig("userPoolId"),
    ClientId: requireConfig("userPoolClientId"),
  });
}

/**
 * Build the Identity Pool login key for a user-pool session. The map key is the
 * provider name `cognito-idp.<region>.amazonaws.com/<userPoolId>` and the value
 * is the user-pool ID token.
 */
function identityPoolLogins(idToken: string): Record<string, string> {
  const provider = `cognito-idp.${requireConfig("region")}.amazonaws.com/${requireConfig(
    "userPoolId"
  )}`;
  return { [provider]: idToken };
}

/**
 * Exchange a user-pool session for an Identity Pool SigV4 credential provider.
 * The provider caches + auto-refreshes creds (fromCognitoIdentityPool behavior).
 */
function credentialsFor(idToken: string): AwsCredentialIdentityProvider {
  return fromCognitoIdentityPool({
    clientConfig: { region: requireConfig("region") },
    identityPoolId: requireConfig("identityPoolId"),
    logins: identityPoolLogins(idToken),
  });
}

/** Build the AuthSession from a completed user-pool session. */
function sessionFrom(user: CognitoUser, session: CognitoUserSession): AuthSession {
  const idToken = session.getIdToken().getJwtToken();
  return {
    idToken,
    username: user.getUsername(),
    credentials: credentialsFor(idToken),
  };
}

/**
 * Sign in to the Cognito User Pool, then derive Identity Pool SigV4 creds.
 *
 * On the seeded operator's FIRST login Cognito returns a
 * newPasswordRequired challenge; this rejects with a NewPasswordRequired whose
 * `complete(newPassword)` finishes the challenge and resolves the AuthSession.
 */
export function signIn(username: string, password: string): Promise<AuthSession> {
  const user = new CognitoUser({ Username: username, Pool: userPool() });
  const authDetails = new AuthenticationDetails({
    Username: username,
    Password: password,
  });

  return new Promise<AuthSession>((resolve, reject) => {
    user.authenticateUser(authDetails, {
      onSuccess: (session) => resolve(sessionFrom(user, session)),
      onFailure: (err) => reject(err),
      newPasswordRequired: (userAttributes) => {
        // Cognito rejects these read-only attributes on the challenge response.
        delete userAttributes.email_verified;
        delete userAttributes.phone_number_verified;
        delete userAttributes.email;
        reject(
          new NewPasswordRequired(
            (newPassword) =>
              new Promise<AuthSession>((res, rej) => {
                user.completeNewPasswordChallenge(newPassword, userAttributes, {
                  onSuccess: (session) => res(sessionFrom(user, session)),
                  onFailure: (e) => rej(e),
                });
              })
          )
        );
      },
    });
  });
}

/**
 * Restore a session for the currently cached user (if any) without prompting
 * for a password, returning null when no valid session exists. Lets a reload
 * reuse the Cognito refresh token instead of forcing a re-login.
 */
export function restoreSession(): Promise<AuthSession | null> {
  const user = userPool().getCurrentUser();
  if (!user) return Promise.resolve(null);
  return new Promise<AuthSession | null>((resolve) => {
    user.getSession((err: Error | null, session: CognitoUserSession | null) => {
      if (err || !session || !session.isValid()) {
        resolve(null);
        return;
      }
      resolve(sessionFrom(user, session));
    });
  });
}

/** Sign the current user out of the local Cognito session. */
export function signOut(): void {
  const user = userPool().getCurrentUser();
  if (user) user.signOut();
}

/** The region used by the SDK clients (shared with the credential provider). */
export function configuredRegion(): string {
  return awsConfig.region;
}

// --------------------------------------------------------------------------- #
// Shared session + lazy credential provider for the DataClient.
// --------------------------------------------------------------------------- #

let currentSession: AuthSession | null = null;

/** Record the active session so the lazy credential provider can use it. */
export function setCurrentSession(session: AuthSession | null): void {
  currentSession = session;
}

/** The active session, if any (set by the login gate). */
export function getCurrentSession(): AuthSession | null {
  return currentSession;
}

/**
 * A lazy SigV4 credential provider for the AWS SDK clients. It resolves the
 * Identity Pool credentials of whatever session is active WHEN A CLIENT FIRST
 * CALLS AWS — so the DirectAwsDataClient can be constructed before login (no
 * network/credentials at construction), and the login gate just needs to call
 * setCurrentSession() before any data call. Throws if no one is signed in.
 */
export function sessionCredentialsProvider(): AwsCredentialIdentityProvider {
  return async (awsIdentityProperties) => {
    const session = currentSession ?? (await restoreSession());
    if (session && session !== currentSession) currentSession = session;
    if (!session) {
      throw new Error(
        "Not signed in: direct-mode AWS access requires a Cognito session."
      );
    }
    return session.credentials(awsIdentityProperties);
  };
}
