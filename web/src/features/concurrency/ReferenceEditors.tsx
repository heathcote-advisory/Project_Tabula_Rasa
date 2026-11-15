import { compareRank, compareText } from '../../components/ordering';
import { FormSection, PageHeader } from '../../components/VisualPrimitives';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type Account, type Schemas } from '../../api/client';
import { accountQuery, resourceKey } from '../../api/query';
import { useEntity } from '../../app/context';
import { ProtectedEditor } from './ProtectedEditor';
import { RecordList } from './RecordList';
import { latest } from './mutations';
import { ActiveField, SelectField, TextField } from './Fields';
import type { UseFormReturn } from 'react-hook-form';

const fixedClassifications: Record<string, string> = { Bank: 'Asset', Cash: 'Asset', 'Credit card': 'Liability', 'Short-term debt': 'Liability', Equity: 'Equity' };

const options = (values: string[]) => values.map(value => ({ id: value, name: value }));
function hasChildren(rows: { parent_id?: string | null }[], id: string) {
  return Boolean(id) && rows.some(row => row.parent_id === id);
}

function eligibleParents<T extends { id: string; parent_id?: string | null }>(rows: T[], id: string, compatible: (row: T) => boolean) {
  if (hasChildren(rows, id)) return [];
  return rows.filter(row => row.id !== id && !row.parent_id && compatible(row));
}

const metadata = (row: { id: string; baseline: string }) => ({ id: row.id, baseline: row.baseline });

function accountLoaded(row: Account) {
  const values: Schemas['AccountChanges'] = { name: row.name, type: row.type, classification: row.classification,
    opening_balance: row.opening_balance, active: row.active, parent_id: row.parent_id ?? '' };
  return { values, metadata: metadata(row) };
}

export function AccountFields({ form, excludeId, creating = false }: { form: UseFormReturn<Schemas['AccountChanges']>; excludeId?: string; creating?: boolean }) {
  const session = useEntity();
  const accounts = useQuery(accountQuery(session));
  const root = hasChildren(accounts.data?.items ?? [], excludeId ?? '');
      const classification = form.formState.defaultValues?.classification ?? form.getValues('classification') ?? 'Asset';
      const fixed = fixedClassifications[form.watch('type') ?? ''];
      return <><FormSection title="Account details"><TextField form={form} name="name" label="Account name" />
      <SelectField form={form} name="type" label="Type" options={options(['Bank', 'Cash', 'Credit card', 'Short-term debt', 'Equity', 'Other'].filter(type => !root || !fixedClassifications[type] || fixedClassifications[type] === classification))}
        onChange={() => {
          const classification = fixedClassifications[form.getValues('type') ?? ''];
          if (classification && classification !== form.getValues('classification')) {
            form.setValue('classification', classification, { shouldDirty: true });
            form.setValue('parent_id', '', { shouldDirty: true });
          }
        }} />
      <SelectField form={form} name="classification" label="Classification" options={options(root ? [classification] : fixed ? [fixed] : ['Asset', 'Liability', 'Equity'])}
        onChange={() => form.setValue('parent_id', '', { shouldDirty: true })} />
      {root && <p className="fieldHint formSpan">Move child accounts before changing classification.</p>}</FormSection>
      <FormSection title="Opening balance and hierarchy"><TextField form={form} name="opening_balance" label="Opening balance" inputMode="decimal"
        hint="Asset opening balances are normally positive. Liability and equity opening ledger balances are normally negative." />
      <SelectField form={form} name="parent_id" label="Parent account" options={[{ id: '', name: 'No parent' },
        ...eligibleParents(accounts.data?.items ?? [], excludeId ?? '', item => item.classification === form.watch('classification'))]} />
      {!creating && form.formState.defaultValues?.active
        ? <p className="muted">Active. Use Deactivate to review and confirm deactivation.</p>
        : !creating && <ActiveField form={form} name="active" />}</FormSection></>;

}

export function AccountEditor({ row, onClose }: { row: Account; onClose: () => void }) {
  const session = useEntity();
  return <ProtectedEditor session={session} loaded={accountLoaded(row)} title="Edit account" onClose={onClose}
    save={async (id, baseline, changes) => accountLoaded(await api.updateAccount(session, id, { baseline, changes }))}
    remove={(id, baseline) => api.deactivateAccount(session, id, baseline)} removeLabel="Deactivate"
    loadLatest={async id => accountLoaded(await latest(api.accounts, session, id))}
    fields={form => <AccountFields form={form} excludeId={row.id} />} />;
}

function categoryLoaded(row: Schemas['Category']) {
  const values: Schemas['CategoryChanges'] = { name: row.name, kind: row.kind, parent_id: row.parent_id ?? '' };
  return { values, metadata: metadata(row) };
}

export function CategoryFields({ form, excludeId }: { form: UseFormReturn<Schemas['CategoryChanges']>; excludeId?: string }) {
  const session = useEntity();
  const categories = useQuery({ queryKey: resourceKey(session, 'categories'), queryFn: ({ signal }) => api.categories(session, signal) });
  return <FormSection title="Category details"><TextField form={form} name="name" label="Category name" />
      <SelectField form={form} name="kind" label="Kind" options={options(hasChildren(categories.data?.items ?? [], excludeId ?? '') ? [form.formState.defaultValues?.kind ?? form.getValues('kind') ?? 'Expense'] : ['Income', 'Expense'])}
        onChange={() => form.setValue('parent_id', '', { shouldDirty: true })} />
      {hasChildren(categories.data?.items ?? [], excludeId ?? '') && <p className="muted">Move child categories before changing kind.</p>}
      <SelectField form={form} name="parent_id" label="Parent category" options={[{ id: '', name: 'No parent' },
        ...eligibleParents(categories.data?.items ?? [], excludeId ?? '', item => item.kind === form.watch('kind'))]} /></FormSection>;
}

function CategoryEditor({ row, onClose }: { row: Schemas['Category']; onClose: () => void }) {
  const session = useEntity();
  return <ProtectedEditor session={session} loaded={categoryLoaded(row)} title="Edit category" onClose={onClose}
    save={async (id, baseline, changes) => categoryLoaded(await api.updateCategory(session, id, { baseline, changes }))}
    remove={(id, baseline) => api.deleteCategory(session, id, baseline)}
    loadLatest={async id => categoryLoaded(await latest(api.categories, session, id))}
    fields={form => <CategoryFields form={form} excludeId={row.id} />} />;
}

function managedLoaded(row: Schemas['ManagedReference']) {
  const values: Schemas['ManagedChanges'] = { name: row.name, active: row.active };
  return { values, metadata: metadata(row) };
}

function ManagedEditor({ row, resource, onClose }: { row: Schemas['ManagedReference']; resource: 'payees' | 'tags'; onClose: () => void }) {
  const session = useEntity();
  const list = resource === 'payees' ? api.payees : api.tags;
  const save = resource === 'payees' ? api.updatePayee : api.updateTag;
  const remove = resource === 'payees' ? api.removePayee : api.removeTag;
  return <ProtectedEditor session={session} loaded={managedLoaded(row)} title={resource === 'payees' ? 'Edit payee' : 'Edit tag'} onClose={onClose}
    confirmSave={(values, defaults) => Boolean(defaults.active && !values.active)}
    save={async (id, baseline, changes) => managedLoaded(await save(session, id, { baseline, changes }))}
    remove={(id, baseline) => remove(session, id, baseline)}
    loadLatest={async id => managedLoaded(await latest(list, session, id))}
    fields={form => <FormSection title="Record details"><TextField form={form} name="name" label="Name" /><ActiveField form={form} name="active" /></FormSection>} />;
}

export function ReferencesPage({ resource }: { resource: 'categories' | 'payees' | 'tags' }) {
  const session = useEntity();
  const title = resource[0].toUpperCase() + resource.slice(1);
  return <><PageHeader title={title} description="Review existing records and save changes for this business."
    actions={session.capabilities.includes('bookkeeping:write') && <Link className="button" to={'/' + resource + '/new'}>Add {resource === 'categories' ? 'category' : resource === 'payees' ? 'payee' : 'tag'}</Link>} />
    {resource === 'categories'
      ? <RecordList resource={resource} title="Categories" list={api.categories} label={row => row.name}
          tree={row => row} sorting={{ defaultCompare: (a, b) => compareRank(a.kind, b.kind, ['Income', 'Expense']) || compareText(a.name, b.name) }}
          columns={[{ title: 'Kind', compare: (a, b) => compareRank(a.kind, b.kind, ['Income', 'Expense']), render: row => <span className="recordBadge">{row.kind}</span> }, { title: 'Parent category', className: 'tableText', render: (row, rows) => rows.find(parent => parent.id === row.parent_id)?.name ?? '—' }]}
          editor={(row, close) => <CategoryEditor row={row} onClose={close} />} />
      : <><RecordList key={resource} resource={resource} title={title} list={resource === 'payees' ? api.payees : api.tags} label={row => row.name}
          columns={[{ title: 'Status', render: row => <span className="recordBadge">{row.active ? 'Active' : 'Inactive'}</span> }]}
          editor={(row, close) => <ManagedEditor row={row} resource={resource} onClose={close} />} />
        <div className="actions"><Link className="button action-secondary" to={'/' + resource + '/merge'}>Merge {resource}</Link></div></>}
  </>;
}
