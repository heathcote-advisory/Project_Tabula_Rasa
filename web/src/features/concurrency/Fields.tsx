import type { HTMLAttributes, ReactNode } from 'react';
import { useWatch, type FieldValues, type Path, type UseFormReturn } from 'react-hook-form';
import { Field } from '../../components/States';

export function TextField<T extends FieldValues>({ form, name, label, type = 'text', onChange, hint, inputMode, multiline = false }: {
  form: UseFormReturn<T>; name: Path<T>; label: string; type?: string; onChange?: () => void;
  hint?: ReactNode; inputMode?: HTMLAttributes<HTMLInputElement>['inputMode']; multiline?: boolean;
}) {
  const error = form.getFieldState(name, form.formState).error?.message;
  const description = [hint ? name + '-hint' : '', error ? name + '-error' : ''].filter(Boolean).join(' ') || undefined;
  return <Field id={name} label={label} error={error}>{multiline
    ? <textarea id={name} aria-invalid={Boolean(error)} aria-describedby={description} {...form.register(name, { onChange })} />
    : <input id={name} type={type} inputMode={inputMode} aria-invalid={Boolean(error)} aria-describedby={description} {...form.register(name, { onChange })} />}
    {hint && <p id={name + '-hint'} className="fieldHint">{hint}</p>}
  </Field>;
}

export function SelectField<T extends FieldValues>({ form, name, label, options, multiple = false, onChange, hint, controlled = false }: {
  form: UseFormReturn<T>; name: Path<T>; label: string; options: { id: string; name: string }[]; multiple?: boolean; onChange?: () => void;
  hint?: ReactNode; controlled?: boolean;
}) {
  const error = form.getFieldState(name, form.formState).error?.message;
  const value = useWatch({ control: form.control, name });
  const selected = multiple && Array.isArray(value) ? options.filter(option => value.includes(option.id)).map(option => option.name) : [];
  const description = [hint || multiple ? name + '-hint' : '', multiple ? name + '-selection' : '', error ? name + '-error' : ''].filter(Boolean).join(' ') || undefined;
  return <Field id={name} label={label} error={error}><select id={name} multiple={multiple} aria-invalid={Boolean(error)}
    aria-describedby={description} {...form.register(name, { onChange })} value={controlled ? value ?? (multiple ? [] : '') : undefined}>
    {options.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}
  </select>
    {(hint || multiple) && <p id={name + '-hint'} className="fieldHint">{hint ?? 'Use Ctrl or Command to select more than one; use Shift to select a range.'}</p>}
    {multiple && <p id={name + '-selection'} className="fieldHint">Selected: {selected.length ? selected.join('; ') : 'None'}</p>}
  </Field>;
}

export function ActiveField<T extends FieldValues>({ form, name }: { form: UseFormReturn<T>; name: Path<T> }) {
  return <label className="checkbox"><input type="checkbox" {...form.register(name)} /> Active</label>;
}
