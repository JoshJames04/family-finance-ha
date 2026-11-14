/**
 * Typed API client — every call is checked end-to-end against the
 * generated OpenAPI schema (`./schema.d.ts`). Requests are anchored
 * to the app mount, including the Home Assistant ingress token.
 */
import { appUrl, isIngress } from "../ingress";
import type { paths } from "./schema";

/** Paths that support a given HTTP method. */
type PathsWith<M extends string> = {
  [P in keyof paths]: paths[P] extends Record<M, unknown> ? P : never;
}[keyof paths];

/** Success status keys present on an operation's responses. */
type SuccessKey<R> = keyof R & (200 | 201 | 202 | 204);

/** JSON response body for path+method's 2xx reply. */
type JsonResponse<
  P extends keyof paths,
  M extends keyof paths[P] & string,
> = paths[P][M] extends { responses: infer R }
  ? R[SuccessKey<R>] extends { content: { "application/json": infer B } }
    ? B
    : never
  : never;

/** JSON request body for path+method, if it takes one. */
type JsonBody<
  P extends keyof paths,
  M extends keyof paths[P] & string,
> = paths[P][M] extends {
  requestBody: { content: { "application/json": infer B } };
}
  ? B
  : never;

/** Query params type for path+method (`never` → omit). */
type QueryOf<
  P extends keyof paths,
  M extends keyof paths[P] & string,
> = paths[P][M] extends { parameters: { query: infer Q } } ? Q
  : paths[P][M] extends { parameters: { query?: infer Q } }
    ? NonNullable<Q> : never;

/** Path params type for templated routes (`never` → omit). */
type PathOf<
  P extends keyof paths,
  M extends keyof paths[P] & string,
> = paths[P][M] extends { parameters: { path: infer Q } } ? Q : never;

interface CallInit<
  P extends keyof paths,
  M extends keyof paths[P] & string,
> {
  path?: PathOf<P, M>;
  query?: QueryOf<P, M>;
  body?: JsonBody<P, M>;
  householdId?: number | null;
}

const HOUSEHOLD_KEY = "ff.householdId";
const PROFILE_KEY = "ff.profileId";

export function getHouseholdId(): number | null {
  const raw = localStorage.getItem(HOUSEHOLD_KEY);
  const id = raw ? Number(raw) : NaN;
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function setHouseholdId(id: number | null): void {
  if (id === null) {
    localStorage.removeItem(HOUSEHOLD_KEY);
  } else {
    localStorage.setItem(HOUSEHOLD_KEY, String(id));
  }
}

export function getProfileId(): number | null {
  const raw = localStorage.getItem(PROFILE_KEY);
  const id = raw ? Number(raw) : NaN;
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function setProfileId(id: number | null): void {
  if (id === null) {
    localStorage.removeItem(PROFILE_KEY);
  } else {
    localStorage.setItem(PROFILE_KEY, String(id));
  }
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly title: string,
    public readonly detail?: string,
  ) {
    super(`${status} ${title}${detail ? ` — ${detail}` : ""}`);
    this.name = "ApiError";
  }
}

async function parseError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as {
      title?: string;
      detail?: string;
    };
    return new ApiError(res.status, body.title ?? res.statusText, body.detail);
  } catch {
    return new ApiError(res.status, res.statusText);
  }
}

/**
 * One typed request. `init.path` fills `{templated}` segments and
 * `init.query` serialises to the query string — both typed from the
 * schema so a wrong key or missing param fails at compile time.
 */
export async function request<
  P extends keyof paths,
  M extends keyof paths[P] & string,
>(
  method: M,
  path: P,
  init?: CallInit<P, M>,
): Promise<JsonResponse<P, M>> {
  let url = String(path);
  const params = init?.path as Record<string, string | number> | undefined;
  for (const [key, value] of Object.entries(params ?? {})) {
    url = url.replace(`{${key}}`, encodeURIComponent(String(value)));
  }
  const query = init?.query as Record<string, unknown> | undefined;
  const qs = Object.entries(query ?? {})
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join("&");
  if (qs) url += `?${qs}`;

  const headers: Record<string, string> = {};
  if (init?.body !== undefined) headers["Content-Type"] = "application/json";
  const hid = init?.householdId ?? getHouseholdId();
  if (hid !== null) headers["X-Household-Id"] = String(hid);
  // Standalone/dev identity only — under ingress the Supervisor
  // injects the HA user identity and a stored header could only
  // contradict it, so it is never sent (#341).
  const pid = isIngress() ? null : getProfileId();
  if (pid !== null) headers["X-Profile-Id"] = String(pid);

  const res = await fetch(appUrl(url), {
    method: method.toUpperCase(),
    headers,
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as JsonResponse<P, M>;
  return (await res.json()) as JsonResponse<P, M>;
}

/** Typed convenience wrappers. */
export const api = {
  get: <P extends PathsWith<"get">>(path: P, init?: CallInit<P, "get">) =>
    request("get", path, init),
  post: <P extends PathsWith<"post">>(path: P, init?: CallInit<P, "post">) =>
    request("post", path, init),
  put: <P extends PathsWith<"put">>(path: P, init?: CallInit<P, "put">) =>
    request("put", path, init),
  patch: <P extends PathsWith<"patch">>(
    path: P,
    init?: CallInit<P, "patch">,
  ) => request("patch", path, init),
  delete: <P extends PathsWith<"delete">>(
    path: P,
    init?: CallInit<P, "delete">,
  ) => request("delete", path, init),
  upload: async (path: string, file: Blob,
                 fields?: Record<string, string>) => {
    const headers: Record<string, string> = {};
    const hid = getHouseholdId();
    const pid = isIngress() ? null : getProfileId();
    if (hid !== null) headers["X-Household-Id"] = String(hid);
    if (pid !== null) headers["X-Profile-Id"] = String(pid);
    const form = new FormData();
    for (const [k, v] of Object.entries(fields ?? {}))
      form.append(k, v);
    form.append("file", file);
    const res = await fetch(appUrl(path), { method: "POST", headers, body: form });
    if (!res.ok) throw await parseError(res);
    return res.json() as Promise<unknown>;
  },
  download: async (path: string) => {
    const headers: Record<string, string> = {};
    const hid = getHouseholdId();
    const pid = isIngress() ? null : getProfileId();
    if (hid !== null) headers["X-Household-Id"] = String(hid);
    if (pid !== null) headers["X-Profile-Id"] = String(pid);
    const res = await fetch(appUrl(path), { headers });
    if (!res.ok) throw await parseError(res);
    return res.blob();
  },
};
