import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const home = readFileSync(resolve(__dirname, '../src/pages/MinimalHomePage.tsx'), 'utf8')
const ptr = readFileSync(resolve(__dirname, '../src/hooks/usePullToRefresh.ts'), 'utf8')
const body = readFileSync(resolve(__dirname, '../src/hooks/useBodyScroll.ts'), 'utf8')

describe('v287 phone Home sticky header', () => {
  it('keeps a fixed phone header outside the PTR translateY shell', () => {
    expect(home).toMatch(/phoneBar[^}]*position: 'fixed'[^}]*zIndex: 50/s)
    expect(home).toContain('data-testid="phone-header"')
    const headerLine = home.split('\n').find(l => l.includes('data-testid="phone-header"'))!
    expect(headerLine).not.toContain('data-shift-root')
    expect(home).toContain('data-shift-root')
  })

  it('restores document scroll (auto) when clearing a stale modal lock', () => {
    expect(ptr).toContain("doc.body.style.overflow = 'auto'")
    expect(ptr).not.toContain("doc.body.style.overflow = ''")
  })

  it('re-asserts body scroll on visibilitychange', () => {
    expect(body).toContain('visibilitychange')
    expect(body).toContain("overflow = 'auto'")
  })
})
