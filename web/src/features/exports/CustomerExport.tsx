import { useState } from 'react';
import { api } from '../../api/client';
import { useEntity } from '../../app/context';
import { ActionButton, SectionHeader } from '../../components/VisualPrimitives';
import { ErrorNotice, Field, LoadingState } from '../../components/States';
import { useCsvDownload } from './useCsvDownload';

export function CustomerExport() {
  const session = useEntity();
  const transfer = useCsvDownload();
  const [scope, setScope] = useState<'metadata_only' | 'documents_inclusive'>('documents_inclusive');
  return <section className="surface" aria-label="Accountant handoff">
    <SectionHeader title="Accountant handoff" description={`Complete bookkeeping archive for ${session.entity.name} · ${session.entity.currency_code}.`} />
    <p>ZIP with all supported canonical bookkeeping collections, instructions and a versioned checksum manifest.
      Audit history, credentials and operational policies are excluded. Records predating audit activation do not have a complete mutation history.</p>
    <Field id="handoff-scope" label="Archive scope"><select id="handoff-scope" value={scope} disabled={transfer.busy}
      onChange={event => setScope(event.target.value as typeof scope)}>
      <option value="documents_inclusive">Bookkeeping and all receipt files</option>
      <option value="metadata_only">Bookkeeping and receipt metadata only</option>
    </select></Field>
    <p>{scope === 'documents_inclusive'
      ? 'Includes linked and unlinked receipt bytes. Missing or unverifiable files prevent delivery; reconnect or recover documents before retrying.'
      : 'Receipt files are excluded. Provider URLs may expire or require separate access; this is not a complete document handoff.'}</p>
    <p>Up to 50,000 records and 200 MiB of package content. File preparation is limited to five minutes.
      No download link or server copy is retained. Save the archive privately; retry requests a fresh snapshot.</p>
    {session.capabilities.includes('bookkeeping:export')
      ? <ActionButton disabled={transfer.busy} onClick={() => void transfer.download(
        signal => api.downloadCustomerExport(session, scope, signal), 'tabula-rasa-handoff.zip')}>Download handoff ZIP</ActionButton>
      : <p>Full export requires Owner, Admin or Accountant access. Read-only and Editor access retain the financial CSV downloads above.</p>}
    {transfer.busy && <LoadingState label="Preparing and checking archive…" />}
    {transfer.error !== undefined && <><ErrorNotice operation="read" error={transfer.error} /><p>Archive unavailable. Check receipt access and integrity, package limits and session access before retrying. No partial archive was delivered.</p></>}
  </section>;
}
