import { expect, test } from "../support/test";

for (const prefix of ["", "/zh-cn"]) {
  test(`tag links resolve in ${prefix === "" ? "English" : prefix}`, async ({ request }) => {
    const old = await request.get(`${prefix}/tags/old-name?q=1`, { maxRedirects: 0 });
    expect(old.status()).toBe(302);
    expect(old.headers()["cache-control"]).toBe("no-store");
    expect(old.headers().location).toBe(`${prefix}/tags/zero-tick?q=1`);
    const current = await request.get(`${prefix}/tags/zero-tick`);
    expect(current.status()).toBe(200);
    expect(await current.text()).toContain("Zero tick");
    expect((await request.get(`${prefix}/tags/missing`)).status()).toBe(404);
  });
}
