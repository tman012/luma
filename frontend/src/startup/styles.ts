/** The app stylesheet loads without blocking the first paint (vite.config.ts nonBlockingCss), so the splash shows at
 *  once. The app renders only after it has applied: no screen is ever laid out, measured or painted unstyled. */
function applied(link: HTMLLinkElement): Promise<void> {
  return new Promise<void>((done) => {
    if (link.dataset.loaded === '1' || link.media === 'all') { done(); return; }
    link.addEventListener('load', () => done(), { once: true });
    link.addEventListener('error', () => done(), { once: true });
  });
}

export function stylesReady(doc: Document = document): Promise<void> {
  const links = Array.from(doc.querySelectorAll<HTMLLinkElement>('link[data-luma-css]'));
  return Promise.all(links.map(applied)).then(() => {});
}
