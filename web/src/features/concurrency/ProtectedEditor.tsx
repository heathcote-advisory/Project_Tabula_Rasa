import { useState, type ReactNode } from 'react';
import { ConfirmationDialog } from '../../components/ConfirmationDialog';
import { useServerFieldErrors } from '../../components/fieldErrors';
import type { FieldValues, UseFormReturn } from 'react-hook-form';
import { ApiError, type Session } from '../../api/client';
import { useProtectedMutation } from './mutations';
import { useLoadedForm, type LoadedForm } from '../../components/useLoadedForm';
import { ErrorNotice, SuccessNotice } from '../../components/States';
import { UnsavedChanges } from '../../components/UnsavedChanges';
import { ActionButton, SectionHeader } from '../../components/VisualPrimitives';

export type LoadMetadata = { id: string; baseline: string };
export type EditorCommand = { label: string; description: string; run: (id: string, baseline: string) => Promise<string> };

export function ReloadPrompt({ pending, onKeep, onReload }: { pending: boolean; onKeep: () => void; onReload: () => void }) {
  return <ConfirmationDialog title="Reload record" keepLabel="Keep draft" confirmLabel="Discard draft and reload"
    pending={pending} onKeep={onKeep} onConfirm={onReload}>
    Reload replaces your draft with the latest saved record. Review it before submitting again.
  </ConfirmationDialog>;
}

export function ProtectedEditor<T extends FieldValues>({ session, loaded, title, fields, save, remove, removeLabel = 'Delete', removeDescription, topBack = true, confirmSave, loadLatest, onClose, commands = [], commandFields, saveConfirmation, uncertainGuidance }: {
  session: Session; loaded: LoadedForm<T, LoadMetadata>; title: string;
  fields: (form: UseFormReturn<T>) => ReactNode;
  save: (id: string, baseline: string, changes: T) => Promise<LoadedForm<T, LoadMetadata>>;
  remove?: (id: string, baseline: string) => Promise<unknown>;
  commands?: EditorCommand[]; commandFields?: ReactNode;
  confirmSave?: (values: T, defaults: Partial<T>) => boolean;
  saveConfirmation?: { title: string; confirmLabel: string; description: string };
  removeDescription?: string; topBack?: boolean;
  removeLabel?: string; loadLatest: (id: string) => Promise<LoadedForm<T, LoadMetadata>>;
  onClose?: () => void;
  uncertainGuidance?: ReactNode;
}) {
  const { form, metadata, reload } = useLoadedForm(loaded);
  const [review, setReview] = useState(false);
  const [pendingSave, setPendingSave] = useState<T>();
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [failure, setFailure] = useState<unknown>();
  const [success, setSuccess] = useState('');
  const [removed, setRemoved] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [closing, setClosing] = useState(false);
  const [pendingCommand, setPendingCommand] = useState<EditorCommand>();
  const mutation = useProtectedMutation(session, async (command: { action: 'save' | 'remove' | 'command'; values?: T; command?: EditorCommand }) => {
    if (!metadata.baseline) throw new ApiError('baseline_required');
    if (command.action === 'remove') {
      await remove!(metadata.id, metadata.baseline);
      setRemoved(true);
      form.reset(form.getValues());
      setSuccess(removeLabel === 'Deactivate' ? 'Account deactivated.' : 'Record deleted.');
    } else if (command.action === 'command') {
      setSuccess(await command.command!.run(metadata.id, metadata.baseline));
      setRemoved(true);
      form.reset(form.getValues());
    } else {
      const next = await save(metadata.id, metadata.baseline, command.values!);
      reload(next);
      setSuccess('Changes saved.');
    }
    setConfirmRemove(false); setPendingSave(undefined); setPendingCommand(undefined);
  });
  async function submit(action: 'save' | 'remove' | 'command', values?: T, command?: EditorCommand) {
    setFailure(undefined); setSuccess('');
    try { await mutation.mutateAsync({ action, values, command }); }
    catch (error) {
      setConfirmRemove(false); setPendingSave(undefined); setPendingCommand(undefined);
      setFailure(error);
    }
  }
  async function reloadLatest() {
    setReloading(true); setSuccess('');
    try {
      const next = await loadLatest(metadata.id);
      reload(next); setFailure(undefined); setReview(false); setConfirmRemove(false); setPendingSave(undefined);
    } catch (error) { setReview(false); setFailure(error); }
    finally { setReloading(false); }
  }
  const pending = mutation.isPending || reloading;
  useServerFieldErrors(form, failure, pending);
  return <section className="surface editor" aria-label={title}>
    {topBack && onClose && <div className="actions"><ActionButton disabled={pending || review} onClick={event => { event.currentTarget.focus(); if (form.formState.isDirty) setClosing(true); else onClose(); }}>Back</ActionButton></div>}
    <SectionHeader title={title} /><UnsavedChanges dirty={form.formState.isDirty} />
    {success && <SuccessNotice>{success}</SuccessNotice>}
    {closing && <ConfirmationDialog title="Discard draft" keepLabel="Keep editing" confirmLabel="Discard draft and close"
      onKeep={() => setClosing(false)} onConfirm={() => { form.reset(form.getValues()); onClose?.(); }}>
      Discard your unsaved changes?
    </ConfirmationDialog>}
    {failure !== undefined && <ErrorNotice error={failure} onReview={() => setReview(true)} />}
    {failure instanceof ApiError && ['unavailable', 'unexpected'].includes(failure.code) && uncertainGuidance}
    {review && <ReloadPrompt pending={pending} onKeep={() => setReview(false)} onReload={() => void reloadLatest()} />}
    {!removed && <form onSubmit={form.handleSubmit(values => {
      if (confirmSave?.(values, form.formState.defaultValues as Partial<T>)) setPendingSave(values);
      else void submit('save', values);
    })}>
      <fieldset disabled={pending || review}>{fields(form)}</fieldset>
      <div className="actions formActions"><ActionButton tone="primary" type="submit" pending={mutation.isPending} disabled={pending || review} onClick={event => event.currentTarget.focus()}>Save changes</ActionButton>
        {onClose && <ActionButton disabled={pending || review} onClick={event => { event.currentTarget.focus(); if (form.formState.isDirty) setClosing(true); else onClose(); }}>Back to records</ActionButton>}
        {remove && <ActionButton tone={removeLabel === 'Delete' ? 'destructive' : 'secondary'} disabled={pending || review} onClick={event => { event.currentTarget.focus(); setConfirmRemove(true); }}>{removeLabel}</ActionButton>}</div>
      {commands.length > 0 && <fieldset className="explicitActions" disabled={pending || review}>
        <legend>Explicit actions</legend>{commandFields}
        <p className="muted">Save any draft changes before an action. After completion, return to the records to load the updated state.</p>
        <div className="actions">{commands.map(command => <ActionButton key={command.label} disabled={form.formState.isDirty}
          onClick={event => { event.currentTarget.focus(); setPendingCommand(command); }}>{command.label}</ActionButton>)}</div>
      </fieldset>}
      {pendingCommand && <ConfirmationDialog title={pendingCommand.label} keepLabel="Keep record"
        confirmLabel={'Confirm ' + pendingCommand.label.toLowerCase()} pending={pending || review}
        onKeep={() => setPendingCommand(undefined)} onConfirm={() => void submit('command', undefined, pendingCommand)}>
        {pendingCommand.description} The saved record must still match the one you loaded.
      </ConfirmationDialog>}
      {pendingSave !== undefined && <ConfirmationDialog title={saveConfirmation?.title ?? 'Deactivate record'} keepLabel="Keep editing"
        confirmLabel={saveConfirmation?.confirmLabel ?? 'Confirm deactivate'} pending={pending || review} onKeep={() => setPendingSave(undefined)}
        onConfirm={() => void submit('save', pendingSave)}>
        {saveConfirmation?.description ?? 'Deactivate this record and save your changes?'} The saved record must still match the one you loaded.
      </ConfirmationDialog>}
      {confirmRemove && <ConfirmationDialog title={removeLabel + ' record'} keepLabel="Keep record"
        confirmTone={removeLabel === 'Delete' ? 'destructive' : 'primary'}
        confirmLabel={'Confirm ' + removeLabel.toLowerCase()} pending={pending || review}
        onKeep={() => setConfirmRemove(false)} onConfirm={() => void submit('remove')}>
        {removeDescription ?? (removeLabel === 'Delete' ? 'Permanently delete this record?' : 'Deactivate this account?')} The saved record must still match the one you loaded.
      </ConfirmationDialog>}
    </form>}
    {removed && onClose && <div className="actions"><ActionButton onClick={onClose}>Back to records</ActionButton></div>}
  </section>;
}
