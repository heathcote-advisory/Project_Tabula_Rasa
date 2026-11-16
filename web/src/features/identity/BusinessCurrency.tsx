import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type Schemas } from '../../api/client';
import { useEntity } from '../../app/context';
import { notifyPeerAuthorityCheck } from '../../app/authSignals';
import { resourceKey } from '../../api/query';
import { Field, ErrorNotice, LoadingState, SuccessNotice } from '../../components/States';
import { ActionButton, FormSection } from '../../components/VisualPrimitives';
import { ConfirmationDialog } from '../../components/ConfirmationDialog';

export function BusinessCurrency() {
  const session = useEntity();
  const settings = useQuery({ queryKey: resourceKey(session, 'business-settings'),
    queryFn: ({ signal }) => api.businessSettings(session, signal) });
  if (!settings.data) return <section className="surface" aria-label="Business currency settings">
    {settings.isPending ? <LoadingState label="Loading business currency…" /> : <ErrorNotice error={settings.error} operation="read" onReview={() => void settings.refetch()} />}
  </section>;
  return <CurrencyForm key={settings.data.version} settings={settings.data} />;
}

function CurrencyForm({ settings }: { settings: Schemas['BusinessSettings'] }) {
  const session = useEntity();
  const [currency, setCurrency] = useState<Schemas['BusinessSettings']['currency_code']>();
  const [review, setReview] = useState<Schemas['BusinessSettings']>();
  const [pending, setPending] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<unknown>();
  const submitting = useRef(false);
  async function save() {
    if (!review || submitting.current) return;
    submitting.current = true;
    setPending(true); setError(undefined);
    try {
      await api.changeBusinessCurrency(session, { ...review, confirm_label_change: true });
      setSaved(true);
    } catch (failure) { setError(failure); }
    finally {
      // A lost response may have committed. Inspect fresh state before another
      // explicit edit; never automatically retry this command.
      setStopped(true); setPending(false); setReview(undefined);
      window.dispatchEvent(new Event('tabula:identity-check'));
      notifyPeerAuthorityCheck();
    }
  }
  const selected = currency ?? settings.currency_code;
  return <section className="surface" aria-label="Business currency settings">
    <FormSection title="Business currency">
      <p>Change the bookkeeping/display unit for {session.entity.name}. Enrollment country and billing are separate.</p>
      <form onSubmit={event => {
        event.preventDefault(); setError(undefined);
        if (selected) setReview({ currency_code: selected, version: settings.version });
      }}>
        <Field id="business-currency" label="Business currency"><select id="business-currency" value={selected}
          disabled={pending || stopped} onChange={event => setCurrency(event.target.value as typeof currency)}>
          <option value="USD">USD — US dollar</option><option value="CAD">CAD — Canadian dollar</option>
        </select></Field>
        <div className="actions"><ActionButton type="submit" disabled={stopped || pending || selected === settings.currency_code}>Save currency</ActionButton></div>
      </form>
    </FormSection>
    {saved && <SuccessNotice>Business currency change confirmed. Refreshing business context…</SuccessNotice>}
    {error !== undefined && <ErrorNotice error={error} />}
    {stopped && <p>Reload Business &amp; members to inspect the current currency before another change. No request is retried automatically.</p>}
    {review && <ConfirmationDialog title="Confirm business currency change" keepLabel="Keep current currency" confirmLabel="Change currency label"
      pending={pending} onKeep={() => setReview(undefined)} onConfirm={() => void save()}>
      <p>{session.entity.name} · Business reference: {session.entity.id}</p>
      <p>All existing balances and transactions keep their exact numeric amounts. Only their currency label/symbol changes.
        No exchange rate or FX conversion occurs: 100.00 {settings.currency_code} becomes 100.00 {review.currency_code}.</p>
      <p>Future entries, reports and exports use {review.currency_code}. This does not change business country or admission status.</p>
    </ConfirmationDialog>}
  </section>;
}
