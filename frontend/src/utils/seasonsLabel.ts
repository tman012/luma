/** "1 season" / "2 seasons" (sentence case, v158a). */
export function seasonsLabel(n: number): string {
  return `${n} season${n === 1 ? '' : 's'}`;
}
