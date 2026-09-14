import { z } from "zod";

import { requestJson } from "@/lib/api/client";

export const authUserSchema = z.object({
  id: z.string().min(1),
  email: z.string().email(),
  display_name: z.string().min(1),
  role: z.enum(["owner", "member"]),
  status: z.enum(["active", "disabled"]),
  is_owner: z.boolean(),
  created_at: z.string().nullable().optional(),
  last_login_at: z.string().nullable().optional(),
});

export type AuthUser = z.infer<typeof authUserSchema>;

const authResponseSchema = z.object({ user: authUserSchema });
const setupStatusSchema = z.object({ setup_required: z.boolean() });
const statusSchema = z.object({ status: z.string() });
const inviteCodeSchema = z.object({
  id: z.string(),
  code: z.string(),
  label: z.string(),
  status: z.enum(["active", "revoked", "expired", "exhausted"]),
  use_count: z.number().int().nonnegative(),
  max_uses: z.number().int().positive().nullable(),
  expires_at: z.string().nullable(),
  created_at: z.string(),
});

export type InviteCode = z.infer<typeof inviteCodeSchema>;

export function getSetupStatus() {
  return requestJson("/auth/setup-status", setupStatusSchema);
}

export function getCurrentUser() {
  return requestJson("/auth/me", authResponseSchema);
}

export function login(email: string, password: string) {
  return requestJson("/auth/login", authResponseSchema, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function bootstrapOwner(email: string, displayName: string, password: string) {
  return requestJson("/auth/bootstrap", authResponseSchema, {
    method: "POST",
    body: JSON.stringify({ email, display_name: displayName, password }),
  });
}

export function logout() {
  return requestJson("/auth/logout", statusSchema, { method: "POST" });
}

export function registerWithInviteCode(code: string, email: string, displayName: string, password: string) {
  return requestJson("/auth/register", z.object({ status: z.string(), user: authUserSchema }), {
    method: "POST",
    body: JSON.stringify({ code, email, display_name: displayName, password }),
  });
}

export function listUsers() {
  return requestJson("/auth/users", z.object({ items: z.array(authUserSchema) }));
}

export function setUserStatus(userId: string, status: "active" | "disabled") {
  return requestJson(`/auth/users/${encodeURIComponent(userId)}/status`, authResponseSchema, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export function listInviteCodes() {
  return requestJson("/auth/invite-codes", z.object({ items: z.array(inviteCodeSchema) }));
}

export function createInviteCode(label: string, expiresInDays: number | null, maxUses: number | null) {
  return requestJson("/auth/invite-codes", z.object({ invite_code: inviteCodeSchema }), {
    method: "POST",
    body: JSON.stringify({ label, expires_in_days: expiresInDays, max_uses: maxUses }),
  });
}

export function revokeInviteCode(inviteCodeId: string) {
  return requestJson(`/auth/invite-codes/${encodeURIComponent(inviteCodeId)}`, statusSchema.extend({
    invite_code_id: z.string(),
  }), { method: "DELETE" });
}

export function resetUserPassword(userId: string) {
  return requestJson(
    `/auth/users/${encodeURIComponent(userId)}/password-reset`,
    z.object({ status: z.literal("success"), password: z.string().min(20) }),
    { method: "POST", body: JSON.stringify({}) },
  );
}
