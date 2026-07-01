import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiFetch,
  getToken,
  loadAuthConfig,
  redeemMagicLink,
  requestMagicLink,
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
  setToken(null);
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
  it("returns the session token on success", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ session_token: "sess.jwt" }));
    await expect(redeemMagicLink("magic")).resolves.toBe("sess.jwt");
  });

  it("throws a friendly message on an invalid link", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 400 }));
    await expect(redeemMagicLink("bad")).rejects.toThrow(/invalid or has expired/);
  });
});
