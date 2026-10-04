export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api" + path, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error("The local service returned an unreadable response.");
  }
  if (!response.ok)
    throw new Error(
      value?.error?.message || "The local request could not be completed.",
    );
  return value as T;
}
export const post = <T>(path: string, value: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(value) });
