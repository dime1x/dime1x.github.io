
export async function onRequestGet({ request, env }) {
  const state = crypto.randomUUID();

  const redirectUri =
    "https://deople.ai/instagram/callback";

  const version = env.GRAPH_API_VERSION;
  const appId = env.META_APP_ID;
  const configId = env.META_CONFIG_ID;

  if (!version || !appId || !configId) {
    return new Response("Missing OAuth configuration", {
      status: 500
    });
  }

  const url = new URL(
    `https://www.facebook.com/${version}/dialog/oauth`
  );

  url.searchParams.set("client_id", appId);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set("config_id", configId);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("state", state);

  return new Response(null, {
    status: 302,
    headers: {
      "Location": url.toString(),
      "Set-Cookie":
        `ig_oauth_state=${state}; HttpOnly; Secure; SameSite=Lax; Path=/instagram; Max-Age=600`
    }
  });
}
