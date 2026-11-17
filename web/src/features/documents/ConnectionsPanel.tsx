import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Schemas } from '../../api/client';
import { resourceKey } from '../../api/query';
import { useEntity } from '../../app/context';
import { ActionButton, SectionHeader } from '../../components/VisualPrimitives';
import { ErrorNotice, LoadingState, SuccessNotice } from '../../components/States';
import { DriveFolderSetup } from './DriveFolderSetup';
import { ConfirmationDialog } from '../../components/ConfirmationDialog';

const labels = { not_configured: 'Not connected', pending: 'Ready for owner connection',
  connected: 'Connected', disconnected: 'Disconnected', reconnect_required: 'Reconnect required' };

export function ConnectionsPanel() {
  const session = useEntity();
  const cache = useQueryClient();
  const refreshConnection = async () => {
    await Promise.all(['connections', 'documents'].map(resource =>
      cache.invalidateQueries({ queryKey: resourceKey(session, resource) })));
  };
  const [confirm, setConfirm] = useState<(Schemas['ConnectionChange'] & { label: string }) | null>(null);
  const query = useQuery({ queryKey: resourceKey(session, 'connections'),
    queryFn: ({ signal }) => api.connectionStatus(session, signal), retry: false });
  const mutation = useMutation({ mutationFn: (command: Schemas['ConnectionChange']) =>
    api.changeConnection(session, command), retry: false,
    onSettled: refreshConnection });
  const authorization = useMutation({ mutationFn: (revision: number) => api.authorizeDrive(session, { revision }),
    retry: false, onSuccess: result => { window.location.assign(result.authorization_url); } });
  const selfService = query.data?.self_service_available;
  const connected = query.data?.status === 'connected';
  const label = connected ? 'Disconnect Google Drive' :
    ['pending', 'not_configured'].includes(query.data?.status ?? '') ? 'Connect Google Drive' : 'Reconnect Google Drive';
  return <section aria-label="External connections">
    <SectionHeader title="Google Drive connection" />
    <p>Receipt storage for {session.entity.name}. Your Google connection is separate from signing in.</p>
    {query.isPending && <LoadingState />}
    {query.error && <ErrorNotice error={query.error} operation="read" onReview={() => void query.refetch()} />}
    {query.data && <p role="status">{labels[query.data.status]}</p>}
    {query.data?.authorized_account && <p>Google account: {query.data.authorized_account}</p>}
    <p>{selfService ? 'Connect your Google account to set up dedicated storage for this business. Reconnect using the same Google account and folders.' :
      'An operator provisions the approved Google grant and receipt folder for this deployment.'}
      {' '}Connect and disconnect require signing in within the last five minutes. If access has expired, sign out and sign in again.</p>
    {query.data?.authorization_status === 'connecting' && <p role="status">Google consent is in progress. Complete consent, or start again if it was interrupted.</p>}
    {(query.data?.authorization_status === 'failed' || new URLSearchParams(window.location.search).get('drive') === 'failed') &&
      <p role="alert">Google Drive setup was not completed. Retry and approve file access. Check Google storage quota and folder permissions. Reconnect the original account for existing receipts; contact support if access cannot be restored.</p>}
    {authorization.error && <ErrorNotice error={authorization.error} onReview={() => void query.refetch()} />}
    {mutation.error && <ErrorNotice error={mutation.error} onReview={() => void query.refetch()} />}
    {mutation.isSuccess && <SuccessNotice>{mutation.data.status === 'connected' ?
      'Google Drive is connected for this business.' : 'Google Drive is disconnected for this business.'}</SuccessNotice>}
    {query.data?.folder_setup && <DriveFolderSetup key={query.data.folder_setup.setup_id} setup={query.data.folder_setup} revision={query.data.revision}
      refresh={refreshConnection} />}
    {query.data && !query.data.folder_setup && (selfService || query.data.status !== 'not_configured') &&
      <ActionButton disabled={query.isFetching || mutation.isPending || authorization.isPending} onClick={() =>
        setConfirm({ revision: query.data!.revision, connect: !connected, label })}>{label}</ActionButton>}
    {confirm && <ConfirmationDialog title={confirm.label} keepLabel="Cancel" confirmLabel={confirm.label}
      pending={mutation.isPending || authorization.isPending} onKeep={() => setConfirm(null)}
      onConfirm={() => selfService && confirm.connect ? authorization.mutate(confirm.revision,
        { onSettled: () => setConfirm(null) }) : mutation.mutate({ revision: confirm.revision, connect: confirm.connect },
        { onSettled: () => setConfirm(null) })}>
      {!confirm.connect ? 'Stop this business from accessing its Google receipts. Files and ledger links are preserved. ' +
        'This disconnect does not revoke Google consent; you can revoke it separately in your Google account.' :
        selfService ? 'Continue to Google to authorize receipt storage for this business. Existing receipt folders and links must remain accessible to the same Google account.' :
          'Enable receipt access using the grant and folder provisioned for this business.'}
    </ConfirmationDialog>}
  </section>;
}
