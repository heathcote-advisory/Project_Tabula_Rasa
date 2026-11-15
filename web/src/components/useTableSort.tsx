import { useState, type ReactNode } from 'react';
import { ActionButton } from './VisualPrimitives';

export function useTableSort<R>(fallback: (a: R, b: R) => number) {
  const [sort, setSort] = useState<{ field: string; descending: boolean }>();
  return {
    compare: (comparators: Record<string, (a: R, b: R) => number>) => (a: R, b: R) =>
      (sort ? comparators[sort.field](a, b) * (sort.descending ? -1 : 1) : fallback(a, b)),
    header: (field: string, label: ReactNode = field, className?: string) => <th scope="col" className={className}
      aria-sort={sort?.field === field ? sort.descending ? 'descending' : 'ascending' : 'none'}>
      <ActionButton onClick={() => setSort({ field, descending: sort?.field === field && !sort.descending })}>
        {label}{sort?.field === field && <span aria-hidden="true"> {sort.descending ? '↓' : '↑'}</span>}</ActionButton></th>,
  };
}
