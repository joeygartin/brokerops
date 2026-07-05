import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiFetch,
  clearSession,
  getToken,
  loadAuthConfig,
  redeemMagicLink,
  refreshSession,
  requestMagicLink,
  setAccessOnlySession,
  setSession,
  setToken,
  setUnauthorizedHandler,
} from "./auth";

// A typed handle on the global fetch we stub per-test.
const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  clearSession(); // wipe both access + refresh tokens between tests
  setUnauthorizedHandler(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("token store", () => {
  it("persists a token to sessionStorage and reads it back", () => {
    setToken("abc123");
    expect(getToken()).toBe("abc123");
    expect(sessionStorage.getItem("brokerops_id_token")).toBe("abc123");
  });

  it("clearing a token wipes sessionStorage", () => {
    setToken("abc123");
    setToken(null);
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem("brokerops_id_token")).toBeNull();
  });
});

describe("apiFetch", () => {
  it("attaches the bearer header when a token is set", async () => {
    setToken("tok");
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await apiFetch("http://api/thing");
    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("omits the bearer header when no token is set", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await apiFetch("http://api/thing");
    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init.headers).get("Authorization")).toBeNull();
  });

  it("clears the token and fires the handler on 401", async () => {
    setToken("stale");
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));

    const response = await apiFetch("http://api/protected");

    expect(response.status).toBe(401);
    expect(getToken()).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("leaves the token intact on a non-401 error", async () => {
    setToken("keep");
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 500 }));
    await apiFetch("http://api/protected");
    expect(getToken()).toBe("keep");
  });
});

describe("apiFetch as the generated client's fetch layer (Request input)", () => {
  it("attaches the bearer to a Request and preserves its method/headers/body", async () => {
    setToken("tok");
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));

    await apiFetch(
      new Request("http://api/thing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hello: "world" }),
      }),
    );

    const [sent] = fetchMock.mock.calls[0] as [Request];
    expect(sent.method).toBe("POST");
    expect(sent.url).toBe("http://api/thing");
    expect(sent.headers.get("Authorization")).toBe("Bearer tok");
    expect(sent.headers.get("Content-Type")).toBe("application/json");
    expect(await sent.json()).toEqual({ hello: "world" });
  });

  it("replays a Request after a 401 refresh with the fresh bearer and intact body", async () => {
    setSession("stale", "ref");
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // original request
      .mockResolvedValueOnce(jsonResponse({ session_token: "fresh" })) // /auth/refresh
      .mockResolvedValueOnce(jsonResponse({ ok: true })); // replay

    const response = await apiFetch(
      new Request("http://api/protected", { method: "POST", body: JSON.stringify({ n: 1 }) }),
    );

    expect(response.status).toBe(200);
    // The replay is a fresh clone: new bearer, same single-use body still readable.
    const [replayed] = fetchMock.mock.calls[2] as [Request];
    expect(replayed.headers.get("Authorization")).toBe("Bearer fresh");
    expect(await replayed.json()).toEqual({ n: 1 });
  });
});

describe("loadAuthConfig", () => {
  it("returns the parsed config on 200", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ enabled: true, methods: ["magic"], client_id: null }),
    );
    await expect(loadAuthConfig()).resolves.toEqual({
      enabled: true,
      methods: ["magic"],
      client_id: null,
    });
  });

  it("throws on a non-ok response", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
    await expect(loadAuthConfig()).rejects.toThrow("503");
  });
});

describe("requestMagicLink", () => {
  it("resolves on 202", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 202 }));
    await expect(requestMagicLink("you@example.com")).resolves.toBeUndefined();
  });

  it("maps 429 to a rate-limit message", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 429 }));
    await expect(requestMagicLink("you@example.com")).rejects.toThrow(/Too many requests/);
  });

  it("throws with the status on other failures", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 400 }));
    await expect(requestMagicLink("you@example.com")).rejects.toThrow("400");
  });
});

describe("redeemMagicLink", () => {
  it("returns the access + refresh tokens on success", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ session_token: "sess.jwt", refresh_token: "ref.jwt" }),
    );
    await expect(redeemMagicLink("magic")).resolves.toEqual({
      access: "sess.jwt",
      refresh: "ref.jwt",
    });
  });

  it("throws a friendly message on an invalid link", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 400 }));
    await expect(redeemMagicLink("bad")).rejects.toThrow(/invalid or has expired/);
  });
});

describe("apiFetch refresh", () => {
  it("renews the access token on 401 and replays the request", async () => {
    setSession("stale", "ref");
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // original request
      .mockResolvedValueOnce(jsonResponse({ session_token: "fresh" })) // /auth/refresh
      .mockResolvedValueOnce(jsonResponse({ ok: true })); // replay

    const response = await apiFetch("http://api/protected");

    expect(response.status).toBe(200);
    expect(getToken()).toBe("fresh");
    expect(onUnauthorized).not.toHaveBeenCalled();
    // The replay carried the refreshed bearer.
    const replayInit = fetchMock.mock.calls[2][1];
    expect(new Headers(replayInit.headers).get("Authorization")).toBe("Bearer fresh");
  });

  it("clears the session and fires the handler when refresh is rejected", async () => {
    setSession("stale", "ref");
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // original request
      .mockResolvedValueOnce(new Response(null, { status: 401 })); // /auth/refresh rejects

    const response = await apiFetch("http://api/protected");

    expect(response.status).toBe(401);
    expect(getToken()).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("does not attempt refresh without a refresh token (Google path)", async () => {
    setToken("google-id-token"); // access only, no refresh
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));

    const response = await apiFetch("http://api/protected");

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledOnce(); // no /auth/refresh call
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("Google login clears a prior refresh token so the Google path never refreshes", async () => {
    setSession("magic-access", "magic-refresh"); // a prior magic session
    setAccessOnlySession("google-id-token"); // Google login replaces it
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));

    const response = await apiFetch("http://api/protected");

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledOnce(); // stale magic refresh token was cleared → no /auth/refresh
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("does not refresh or replay a stale request after a replacement login", async () => {
    setSession("A-access", "A-refresh");
    let resolve401!: (r: Response) => void;
    const deferred = new Promise<Response>((resolve) => {
      resolve401 = resolve;
    });
    fetchMock.mockReturnValueOnce(deferred); // original request, held open

    const inFlight = apiFetch("http://api/protected");
    setSession("B-access", "B-refresh"); // replacement login lands mid-flight
    resolve401(new Response(null, { status: 401 }));

    const response = await inFlight;
    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledOnce(); // no /auth/refresh, no replay under the new session
    expect(getToken()).toBe("B-access"); // new session left intact
  });

  it("does not tear down a replacement session via a stale in-flight refresh", async () => {
    // Session A starts a refresh that never resolves in time; session B replaces A;
    // a B request 401s and must run its OWN refresh (with B's token) and replay —
    // not reuse A's stale promise and then clear B.
    setSession("A-access", "A-refresh");
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);

    let resolveA!: (r: Response) => void;
    const deferredA = new Promise<Response>((resolve) => {
      resolveA = resolve;
    });
    fetchMock.mockReturnValueOnce(deferredA); // A's /auth/refresh — held open
    const aRefresh = refreshSession();

    setSession("B-access", "B-refresh"); // B replaces A while A's refresh is in flight

    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // B request
      .mockResolvedValueOnce(jsonResponse({ session_token: "B-fresh" })) // B's /auth/refresh
      .mockResolvedValueOnce(jsonResponse({ ok: true })); // B replay
    const response = await apiFetch("http://api/protected");

    expect(response.status).toBe(200);
    expect(getToken()).toBe("B-fresh"); // B refreshed on its own token, not cleared
    expect(onUnauthorized).not.toHaveBeenCalled();

    // A's late refresh must be discarded, leaving B intact.
    resolveA(jsonResponse({ session_token: "A-fresh" }));
    await expect(aRefresh).resolves.toBe(false);
    expect(getToken()).toBe("B-fresh");
  });

  it("does not resurrect a signed-out session when a refresh resolves late", async () => {
    // Sign-out lands while a refresh is in flight; when the refresh finally
    // resolves 200, it must be discarded — never write a token or replay.
    setSession("stale", "ref");
    let resolveRefresh!: (r: Response) => void;
    const deferred = new Promise<Response>((resolve) => {
      resolveRefresh = resolve;
    });
    fetchMock.mockReturnValueOnce(deferred); // /auth/refresh — held open

    const inFlight = refreshSession();
    clearSession(); // operator signs out mid-refresh
    resolveRefresh(jsonResponse({ session_token: "fresh" }));

    await expect(inFlight).resolves.toBe(false);
    expect(getToken()).toBeNull(); // not resurrected
  });
});
