// Typed fetch helper for the Advisor Copilot platform UI.
// The UI talks only to the FastAPI BFF; AWS credentials stay server-side.

export const BFF_BASE_URL =
  import.meta.env.VITE_BFF_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

/** GET a JSON resource from the BFF and type the result. */
export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BFF_BASE_URL}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new ApiError(res.status, `GET ${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

/** POST a JSON body to the BFF and type the result. */
export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BFF_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(res.status, `POST ${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}
