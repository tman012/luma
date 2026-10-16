// Tiny DOM stand-in for unit tests of browser helpers (no dependencies).
function makeEl(attrs = {}) {
  const a = { ...attrs };
  return {
    style: {},
    hasAttribute: (k) => k in a,
    getAttribute: (k) => (k in a ? a[k] : null),
    setAttribute: (k, v) => { a[k] = String(v); },
    removeAttribute: (k) => { delete a[k]; },
    attrs: a,
  };
}
function makeDoc({ overlays = [], rootAttrs = {} } = {}) {
  const root = makeEl(rootAttrs);
  return {
    body: makeEl(), documentElement: makeEl(), root,
    getElementById: (id) => (id === 'root' ? root : null),
    querySelector: (sel) => (overlays.some((o) => sel.includes(o)) ? makeEl() : null),
  };
}
module.exports = { makeEl, makeDoc };
