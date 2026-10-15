import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const DEFAULT_AUTH_PATH = path.join(os.homedir(), ".grok", "auth.json");
const TOKEN_URL = "https://auth.x.ai/oauth2/token";
const SKEW_MS = 60_000;

export function readSession(raw) {
  if (!raw || typeof raw !== "object") return null;
  const entries = Object.entries(raw);
  const preferred = entries.find(([, value]) => value?.auth_mode === "oidc" && value?.key);
  const fallback = entries.find(([, value]) => typeof value?.key === "string" && value.key);
  const chosen = preferred || fallback;
  if (!chosen) return null;
  return { storageKey: chosen[0], ...chosen[1] };
}

export function tokenIsFresh(expiresAt, now = Date.now()) {
  const time = Date.parse(expiresAt || "");
  return Number.isFinite(time) && time > now + SKEW_MS;
}

async function refreshSession(session, fetchImpl) {
  const body = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: session.refresh_token,
    client_id: session.oidc_client_id,
  });
  const response = await fetchImpl(TOKEN_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Accept: "application/json",
    },
    body,
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = JSON.parse(text);
  } catch {
    payload = null;
  }
  if (!response.ok || !payload?.access_token) {
    const reason = payload?.error_description || payload?.error || `HTTP ${response.status}`;
    throw new Error(`Could not refresh the xAI subscription session (${reason}). Sign in with Grok again.`);
  }
  return payload;
}

function writeAuth(authPath, raw) {
  const temp = `${authPath}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(raw, null, 2));
  try {
    fs.renameSync(temp, authPath);
  } catch {
    fs.copyFileSync(temp, authPath);
    fs.unlinkSync(temp);
  }
}

export function createSubscriptionAuth({ authPath = DEFAULT_AUTH_PATH, fetchImpl = fetch } = {}) {
  let memory = null;

  function sessionFile() {
    try {
      return readSession(JSON.parse(fs.readFileSync(authPath, "utf8")));
    } catch {
      return null;
    }
  }

  return {
    describe() {
      const session = sessionFile();
      if (!session) return { signedIn: false, name: null };
      return { signedIn: true, name: session.first_name || null };
    },

    async getAccessToken({ force = false } = {}) {
      if (!force && memory && memory.expiresAt > Date.now() + SKEW_MS) return memory.token;

      const raw = JSON.parse(fs.readFileSync(authPath, "utf8"));
      const session = readSession(raw);
      if (!session?.key) {
        throw new Error("No xAI subscription session. Sign in with Grok, then start the voice app again.");
      }
      if (!force && tokenIsFresh(session.expires_at)) {
        memory = {
          token: session.key,
          expiresAt: Date.parse(session.expires_at),
          name: session.first_name || null,
        };
        return session.key;
      }
      if (!session.refresh_token || !session.oidc_client_id) {
        throw new Error("The xAI subscription session expired. Sign in with Grok again.");
      }

      const refreshed = await refreshSession(session, fetchImpl);
      const expiresAt = Date.now() + (Number(refreshed.expires_in) || 3600) * 1000;
      writeAuth(authPath, {
        ...raw,
        [session.storageKey]: {
          ...raw[session.storageKey],
          key: refreshed.access_token,
          refresh_token: refreshed.refresh_token || session.refresh_token,
          expires_at: new Date(expiresAt).toISOString(),
          create_time: new Date().toISOString(),
        },
      });
      memory = { token: refreshed.access_token, expiresAt, name: session.first_name || null };
      return refreshed.access_token;
    },
  };
}
