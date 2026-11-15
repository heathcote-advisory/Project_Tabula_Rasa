import { useId } from 'react';

export function DescriptionPreview({ text }: { text: string }) {
  const id = useId();
  return <>
    <button type="button" className="descriptionPreview" title={text} aria-label={text || 'No description'} popoverTarget={id}>
      <span>{text || '—'}</span>
    </button>
    <div id={id} className="descriptionDisclosure" popover="auto" role="tooltip">{text || 'No description'}</div>
  </>;
}
