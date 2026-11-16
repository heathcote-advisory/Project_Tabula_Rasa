import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, type Session } from '../../api/client';
import { resourceKey } from '../../api/query';

const affectedResources = ['vehicles', 'accounts', 'categories', 'payees', 'tags', 'transactions', 'journals', 'mileage', 'documents', 'ledger', 'reports', 'assets', 'recurring', 'mixed-use'];

export function useProtectedMutation<T, R = unknown>(session: Session, mutationFn: (data: T) => Promise<R>) {
  const cache = useQueryClient();
  return useMutation({ mutationFn, retry: false, onSettled: () => Promise.all(
    affectedResources.map(resource => cache.invalidateQueries({ queryKey: resourceKey(session, resource) })),
  ) });
}

export async function latest<R extends { id: string }>(list: (session: Session) => Promise<{ items: R[] }>, session: Session, id: string) {
  const response = await list(session);
  const row = response.items.find(item => item.id === id);
  if (!row) throw new ApiError('not_found');
  return row;
}
