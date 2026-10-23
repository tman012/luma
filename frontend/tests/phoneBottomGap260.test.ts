import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// v259 Tester LOW: prod 1b4ea74's phone Home ends with the last rail's 28px bottom margin, then the 48px spacer.
const src = readFileSync(resolve(__dirname, '../src/pages/MinimalHomePage.tsx'), 'utf8')

describe('phone Home bottom matches prod', () => {
  it('has 28px after the last rail, then the 48px spacer', () => {
    expect(src).toMatch(/<\/main>\s*\{\/\*[\s\S]*?\*\/\}\s*<div style=\{\{ height: 28 \}\} data-testid="phone-last-rail-gap" \/>\s*<div style=\{\{ height: 48 \}\} \/>/)
  })
})
