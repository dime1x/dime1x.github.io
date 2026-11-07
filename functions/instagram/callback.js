
async function encryptToken(token, secret) {
  const rawKey = Uint8Array.from(
    atob(secret), c => c.charCodeAt(0)
  );

  const key = await crypto.subtle.importKey(
    "raw",
    rawKey,
    "AES-GCM",
    false,
    ["encrypt"]
  );

  const iv = crypto.getRandomValues(new Uint8Array(12));

  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    new TextEncoder().encode(token)
  );

  return JSON.stringify({
    iv: btoa(String.fromCharCode(...iv)),
    data: btoa(String.fromCharCode(
      ...new Uint8Array(encrypted)
    ))
  });
}

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);

  const error = url.searchParams.get("error");
  if (error) {
    return new Response("Instagram authorization failed", {
      status: 400
    });
  }

  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");

  const cookie = request.headers.get("Cookie") || "";
  const savedState = cookie.match(
    /(?:^|;\s*)ig_oauth_state=([^;]+)/
  )?.[1];

  const headers = {
    "Content-Type": "text/plain; charset=utf-8",
    "Set-Cookie":
      "ig_oauth_state=; HttpOnly; Secure; SameSite=Lax; Path=/instagram; Max-Age=0"
  };

  if (!code || !state || !savedState ||
      state !== savedState) {
    return new Response("Invalid OAuth state or code", {
      status: 400,
      headers
    });
  }

  const redirectUri =
    "https://deople.ai/instagram/callback";

  const tokenUrl = new URL(
    `https://graph.facebook.com/${env.GRAPH_API_VERSION}/oauth/access_token`
  );

  tokenUrl.searchParams.set("client_id", env.META_APP_ID);
  tokenUrl.searchParams.set("client_secret", env.META_APP_SECRET);
  tokenUrl.searchParams.set("redirect_uri", redirectUri);
  tokenUrl.searchParams.set("code", code);

  const result = await fetch(tokenUrl);

  const data = await result.json();

  if (!result.ok || !data.access_token) {
    return new Response(
      "Token exchange failed. Check server configuration.",
      { status: 400, headers }
    );
  }

  if (!env.INSTAGRAM_TOKENS || !env.TOKEN_ENCRYPTION_KEY) {
    return new Response("Token storage is not configured", {
      status: 500,
      headers
    });
  }

  const encrypted = await encryptToken(
    data.access_token,
    env.TOKEN_ENCRYPTION_KEY
  );

  await env.INSTAGRAM_TOKENS.put(
    "instagram_primary",
    encrypted
  );

  return new Response(
    "Instagram authorization successful. Token stored securely.",
    { headers }
  );
}
