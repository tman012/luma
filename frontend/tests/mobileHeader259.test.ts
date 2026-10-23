import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// v259: the phone header is copied from prod 1b4ea74's AuthenticatedHomePage TopBar.
const src = readFileSync(resolve(__dirname, '../src/pages/MinimalHomePage.tsx'), 'utf8')
const menu = readFileSync(resolve(__dirname, '../src/components/nav/UserMenu.tsx'), 'utf8')

describe('v259 phone header matches prod', () => {
  it('uses prod logo asset and size', () => {
    expect(src).toContain("'/static/images/luma_text.webp'")
    expect(src).toMatch(/phoneLogo[^}]*height: 100[^}]*objectFit: 'contain'[^}]*marginTop: -2/s)
  })
  it('is a solid fixed bar at z-index 50 with a 56px row', () => {
    expect(src).toMatch(/phoneBar[^}]*position: 'fixed'[^}]*zIndex: 50/s)
    expect(src).toMatch(/phoneBar[^}]*background: 'var\(--color-kiosk-black\)'/s)
    expect(src).toMatch(/phoneRow[^}]*height: 56/s)
    expect(src).toContain("paddingTop: 'calc(56px + env(safe-area-inset-top, 0px) + 16px)'")
  })
  it('search button is prod 46px bare button', () => {
    expect(src).toMatch(/searchBtn[^}]*width: 46[^}]*height: 46/s)
  })
  it('avatar trigger is prod 42px person glyph', () => {
    expect(menu).toContain('M2.5 16c0-3.314 2.91-6 6.5-6s6.5 2.686 6.5 6')
  })
})
