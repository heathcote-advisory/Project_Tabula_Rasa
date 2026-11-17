import { useEffect, useRef, useState } from 'react';
import { api, type Schemas } from '../../api/client';
import { useEntity } from '../../app/context';
import { ActionButton } from '../../components/VisualPrimitives';
import { ErrorNotice, Field } from '../../components/States';
import { pickDriveFolder } from './drivePicker';

export function DriveFolderSetup({ setup, revision, refresh }: {
  setup: Schemas['DriveFolderSetup'];
  revision: number;
  refresh: () => Promise<void>;
}) {
  const session = useEntity();
  const [name, setName] = useState(setup.default_name);
  const [folder, setFolder] = useState<{ id: string; name: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [pickerError, setPickerError] = useState(false);
  const pickerRequest = useRef<AbortController | null>(null);
  useEffect(() => () => pickerRequest.current?.abort(), []);

  async function selectFolder() {
    const request = new AbortController();
    pickerRequest.current = request;
    setBusy(true);
    setPickerError(false);
    setError(undefined);
    try {
      const { access_token, developer_key, project_number } = await api.setupDrive(session, {
        action: 'picker', revision, setup_id: setup.setup_id,
      }, request.signal);
      if (!access_token || !developer_key || !project_number) throw new Error('Picker unavailable');
      const selected = await pickDriveFolder(access_token, developer_key, project_number, request.signal);
      if (selected) setFolder(selected);
    } catch {
      if (!request.signal.aborted) setPickerError(true);
    } finally {
      pickerRequest.current = null;
      setBusy(false);
    }
  }

  async function submit(action: 'create' | 'cancel') {
    setBusy(true);
    setError(undefined);
    setPickerError(false);
    try {
      await api.setupDrive(session, { action, revision, setup_id: setup.setup_id, ...(action === 'create' && !setup.reserved
        ? { name: name.trim(), parent_id: folder?.id } : {}) });
    } catch (cause) {
      setError(cause);
    } finally {
      await refresh();
      setBusy(false);
    }
  }

  return <form onSubmit={event => { event.preventDefault(); void submit('create'); }}>
    <h3>New Tabula Rasa storage folder</h3>
    {setup.reserved ? <p>Resume setup with the Google account, folder name and destination you already confirmed.</p> :
      <>
        <Field id="drive-storage-name" label="Folder name">
          <input id="drive-storage-name" required maxLength={255} disabled={busy} value={name}
            onChange={event => setName(event.target.value)} />
        </Field>
        <p>Destination: {folder?.name ?? 'My Drive root'}</p>
        <div className="actions">
          {setup.picker_available && <ActionButton type="button" disabled={busy}
            onClick={() => void selectFolder()}>Choose Drive folder</ActionButton>}
          {folder && <ActionButton type="button" disabled={busy}
            onClick={() => setFolder(undefined)}>Use My Drive root</ActionButton>}
        </div>
      </>}
    {pickerError && <p role="alert">Google Picker could not open. Try again or choose My Drive root.</p>}
    {error != null && <ErrorNotice error={error} onReview={() => void refresh()} />}
    <div className="actions">
      <ActionButton type="submit" disabled={busy || (!setup.reserved && !name.trim())}>
        {setup.reserved ? 'Resume folder setup' : 'Create storage folder'}
      </ActionButton>
      <ActionButton type="button" disabled={busy} onClick={() => void submit('cancel')}>Cancel setup</ActionButton>
    </div>
  </form>;
}
