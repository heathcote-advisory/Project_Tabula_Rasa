export const compareText = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
// Compare decimal strings without losing cents to floating-point conversion.
export function compareDecimal(a: string, b: string) {
  const parts = (s: string) => { const [whole, fraction = ''] = s.split('.'); return { value: BigInt(whole + fraction), scale: fraction.length }; };
  const left = parts(a), right = parts(b);
  const x = left.value * 10n ** BigInt(right.scale), y = right.value * 10n ** BigInt(left.scale);
  return x < y ? -1 : x > y ? 1 : 0;
}
export function compareRank(a: string, b: string, order: string[]) {
  const rank = (s: string) => order.includes(s) ? order.indexOf(s) : order.length;
  return rank(a) - rank(b) || compareText(a, b);
}
export type TreeRecord = { id: string; name: string; parent_id?: string | null };
export function hierarchy<R extends TreeRecord>(rows: R[], compare: (a: R, b: R) => number = (a, b) => compareText(a.name, b.name)) {
  const ordered = [...rows].sort((a, b) => compare(a, b) || compareText(a.name, b.name) || compareText(a.id, b.id));
  const ids = new Set(rows.map(row => row.id)), visited = new Set<string>();
  const children = new Map<string, R[]>();
  for (const row of ordered) { const key = row.parent_id || ''; children.set(key, [...(children.get(key) ?? []), row]); }
  const result: { row: R; depth: number; parent?: string }[] = [];
  function visit(row: R, depth: number) {
    if (visited.has(row.id)) return;
    visited.add(row.id); result.push({ row, depth, parent: rows.find(parent => parent.id === row.parent_id)?.name });
    for (const child of children.get(row.id) ?? []) visit(child, depth + 1);
  }
  for (const row of ordered.filter(row => !row.parent_id || !ids.has(row.parent_id))) visit(row, 0);
  // Retain malformed/orphaned records deterministically rather than hiding them.
  for (const row of ordered) visit(row, 0);
  return result;
}
