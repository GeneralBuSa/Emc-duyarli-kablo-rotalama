// Interface language of the viewer page (Turkish / English), from viewer/i18n.json.
// This classic script runs before the large three.js bundle, so the static text is in the chosen language
// from the first paint; viewer.js uses window.__i18n for everything it renders itself.
(() => {
  const { strings: DICT, vars: VARS } = JSON.parse(document.getElementById("i18n").textContent);
  const LANGS = Object.keys(DICT);
  const capture = new URLSearchParams(location.search).has("capture");   // README images are always Turkish
  let saved = null;
  try { saved = localStorage.getItem("emc-kablo:lang"); } catch (e) { /* storage blocked: follow the browser */ }
  const browser = (navigator.languages || [navigator.language || ""]).some(l => /^tr\b/i.test(l)) ? "tr" : "en";
  let lang = capture ? "tr" : LANGS.includes(saved) ? saved : browser;

  // {name} placeholders are filled from `v`, then from the page-wide values (e.g. {n} = number of cables).
  const own = (o, k) => (Object.hasOwn(o, k) ? o[k] : undefined);
  const t = (key, v = {}) => (own(DICT[lang], key) ?? own(DICT.tr, key) ?? key)
    .replace(/\{(\w+)\}/g, (m, k) => (Object.hasOwn(v, k) ? v[k] : Object.hasOwn(VARS, k) ? VARS[k] : m));

  function apply() {
    document.documentElement.lang = lang;
    for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
    for (const el of document.querySelectorAll("[data-i18n-html]")) el.innerHTML = t(el.dataset.i18nHtml);   // own strings only
    for (const el of document.querySelectorAll("[data-i18n-aria]")) el.setAttribute("aria-label", t(el.dataset.i18nAria));
    for (const el of document.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
    document.title = t("meta.title");
    document.querySelector('meta[name="description"]').setAttribute("content", t("meta.description"));
  }

  const listeners = [];
  window.__i18n = {
    get lang() { return lang; },
    langs: LANGS,
    t,
    has: key => Object.hasOwn(DICT[lang], key),
    locale: () => (lang === "tr" ? "tr-TR" : "en-US"),
    set(next) {
      if (!LANGS.includes(next) || next === lang) return;
      lang = next;
      try { localStorage.setItem("emc-kablo:lang", next); } catch (e) { /* the choice still applies to this visit */ }
      apply();
      for (const f of listeners) f(lang);
    },
    onChange(f) { listeners.push(f); },
  };
  apply();
})();
