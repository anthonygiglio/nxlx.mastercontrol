// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Take a page's route off only when no answer from it is still on its way.
//
// A handler that awaits route.fetch() and then calls route.fulfill() is mid-flight for a few milliseconds per request. If
// page.unroute() takes the last route off in that time, Playwright switches the interception off and the browser carries
// the request on by itself; the fulfill that follows then throws "Route is already handled!" as an unhandled rejection
// that ends the run. (Reproduced on the harness: 86 of them in 40 take-offs with a handler that needed 40 ms.)
//
// patchUnroute(page) changes page.route and page.unroute (for every page: it changes the class). A route that is being
// taken off first stops taking new requests (they go on to the next route, or to the node, as they will anyway), then
// page.unroute waits for the calls it still has in flight, and only then takes it off. A call that never ends (a handler
// that holds a request on purpose) is waited for up to `limit` ms, then the route is taken off anyway.
// Nothing is swallowed: a handler that really answers twice still fails.
function patchUnroute(page, limit = 5000) {
  const proto = Object.getPrototypeOf(page);
  if (proto.__routesCalm) return;
  const route = proto.route, unroute = proto.unroute;
  const records = new WeakMap();           // page -> [{ url, handler, w, retiring, flying: Set }]
  const of = (pg) => { if (!records.has(pg)) records.set(pg, []); return records.get(pg); };
  proto.route = function (url, handler, options) {
    if (typeof handler !== 'function') return route.call(this, url, handler, options);
    const rec = { url, handler, retiring: false, flying: new Set() };
    rec.w = (r, req) => {
      if (rec.retiring) return r.fallback();
      const run = Promise.resolve().then(() => handler(r, req));
      const done = run.then(() => {}, () => {});
      rec.flying.add(done);
      done.then(() => rec.flying.delete(done));
      return run;
    };
    of(this).push(rec);
    return route.call(this, url, rec.w, options);
  };
  proto.unroute = async function (url, handler) {
    const gone = of(this).filter((x) => x.url === url && (!handler || x.handler === handler));
    for (const x of gone) x.retiring = true;
    const end = Date.now() + limit;
    for (const x of gone) {
      while (x.flying.size && Date.now() < end) {
        await Promise.race([Promise.all(Array.from(x.flying)), new Promise((r) => setTimeout(r, Math.max(1, end - Date.now())))]);
      }
    }
    const rest = of(this).filter((x) => !gone.includes(x));
    records.set(this, rest);
    if (!gone.length) return unroute.call(this, url, handler);
    for (const x of gone) await unroute.call(this, url, x.w);
  };
  proto.__routesCalm = true;
}

module.exports = { patchUnroute };
