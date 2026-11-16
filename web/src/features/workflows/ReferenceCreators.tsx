import { useNavigate } from 'react-router-dom';
import { api, type Schemas } from '../../api/client';
import { useEntity } from '../../app/context';
import { AccountFields, CategoryFields } from '../concurrency/ReferenceEditors';
import { ActiveField, TextField } from '../concurrency/Fields';
import { CreateEditor } from './CreateEditor';
import { FormSection, PageHeader } from '../../components/VisualPrimitives';

export function ReferenceCreator({ resource }: { resource: 'accounts' | 'categories' | 'payees' | 'tags' | 'vehicles' }) {
  const session = useEntity();
  const navigate = useNavigate();
  const close = () => navigate(resource === 'vehicles' ? '/mileage/vehicles' : '/' + resource);
  if (!session.capabilities.includes('bookkeeping:write')) return <p role="alert">This business is read-only.</p>;
  if (resource === 'accounts') return <><PageHeader title="Add account" /><CreateEditor<Schemas['AccountChanges']> title="Create account" onClose={close}
    initial={() => ({ name: '', type: 'Bank', classification: 'Asset', opening_balance: '0.00', parent_id: '', active: true })}
    create={values => api.createAccount(session, { name: values.name, type: values.type, classification: values.classification,
      opening_balance: values.opening_balance, parent_id: values.parent_id })}
    fields={form => <AccountFields form={form} creating />} /></>;
  if (resource === 'categories') return <><PageHeader title="Add category" /><CreateEditor<Schemas['CategoryChanges']> title="Create category" onClose={close}
    initial={() => ({ name: '', kind: 'Expense', parent_id: '' })} create={values => api.createCategory(session, values)}
    fields={form => <CategoryFields form={form} />} /></>;
  return <><PageHeader title={'Add ' + (resource === 'vehicles' ? 'vehicle' : resource === 'payees' ? 'payee' : 'tag')} /><CreateEditor<Schemas['ManagedChanges']> key={resource} title={'Create ' + (resource === 'vehicles' ? 'vehicle' : resource === 'payees' ? 'payee' : 'tag')} onClose={close}
    initial={() => ({ name: '', active: true })}
    create={values => (resource === 'vehicles' ? api.createVehicle : resource === 'payees' ? api.createPayee : api.createTag)(session, values)}
    fields={form => <FormSection title="Record details"><TextField form={form} name="name" label="Name" /><ActiveField form={form} name="active" /></FormSection>} /></>;
}
