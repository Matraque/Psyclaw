export type ConnectionConfig =
  | {
      mode: "connected";
      adkUrl: string;
      appName: string;
      userId: string;
    }
  | { mode: "demo" }
  | { mode: "disconnected"; missing: string[] };

type PublicEnvironment = Record<string, string | boolean | undefined>;

const keys = ["VITE_ADK_URL", "VITE_ADK_APP_NAME", "VITE_ADK_USER_ID"] as const;

function isLoopbackHost(hostname: string) {
  if (hostname === "localhost" || hostname === "::1" || hostname === "[::1]") return true;

  const parts = hostname.split(".");
  return (
    parts.length === 4 &&
    parts[0] === "127" &&
    parts.every((part) => /^\d+$/.test(part) && Number(part) <= 255)
  );
}

function isLocalAdkUrl(value: string) {
  try {
    const url = new URL(value);
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      isLoopbackHost(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}

function readString(environment: PublicEnvironment, key: (typeof keys)[number]) {
  const value = environment[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function getConnectionConfig(
  environment: PublicEnvironment = import.meta.env,
): ConnectionConfig {
  if (environment.VITE_PSYCLAW_DEMO === "true") return { mode: "demo" };

  const adkUrl = readString(environment, "VITE_ADK_URL");
  const appName = readString(environment, "VITE_ADK_APP_NAME");
  const userId = readString(environment, "VITE_ADK_USER_ID");
  const validAdkUrl = adkUrl && isLocalAdkUrl(adkUrl);

  if (validAdkUrl && appName && userId) {
    return { mode: "connected", adkUrl, appName, userId };
  }

  return {
    mode: "disconnected",
    missing: keys.filter(
      (key) =>
        !readString(environment, key) || (key === "VITE_ADK_URL" && !validAdkUrl),
    ),
  };
}
