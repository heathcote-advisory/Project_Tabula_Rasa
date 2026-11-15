import { expect, it } from 'vitest';
import { compareDecimal, compareRank, compareText, hierarchy } from './ordering';

it('keeps nested children with their parents and retains unknown kinds, orphans and cycles', () => {
  const rows = [
    { id: 'c', name: 'A child', parent_id: 'p', kind: 'Income' },
    { id: 'expense', name: 'A expense', kind: 'Expense' },
    { id: 'p', name: 'Z income', kind: 'Income' },
    { id: 'grand', name: 'Grandchild', parent_id: 'c', kind: 'Income' },
    { id: 'other', name: 'Other', kind: 'Other' },
    { id: 'orphan', name: 'Orphan', parent_id: 'missing', kind: 'Other' },
    { id: 'cycle', name: 'Cycle', parent_id: 'cycle', kind: 'Other' },
  ];
  const order = hierarchy(rows, (a, b) => compareRank(a.kind, b.kind, ['Income', 'Expense']) || compareText(a.name, b.name));
  expect(order.map(item => item.row.id)).toEqual(['p', 'c', 'grand', 'expense', 'orphan', 'other', 'cycle']);
  expect(order.slice(0, 3).map(item => item.depth)).toEqual([0, 1, 2]);
  expect(order[2].parent).toBe('A child');
});
it('compares decimal fields exactly, including signs, different scales and large amounts', () => {
  expect(compareDecimal('9007199254740992.01', '9007199254740992.02')).toBe(-1);
  expect(compareDecimal('-10.00', '-2')).toBe(-1);
  expect(compareDecimal('2.0', '2.00')).toBe(0);
  expect(compareDecimal('10.01', '2.00')).toBe(1);
});
